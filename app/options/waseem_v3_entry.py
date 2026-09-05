from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class EntryPlan:
    state: str
    entry_low: float
    entry_high: float
    current_price: float
    entry_quality: float
    chase_risk: bool
    reason: str
    diagnostics: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


class WaseemV3EntryEngine:
    """Execution-quality layer used ONLY by Waseem V3.

    V1/V2 contract ranking is left untouched. V3 takes an already-ranked
    contract and decides whether the premium is efficient enough to enter now
    or should be watched for a better fill. The desired price is derived from
    the live bid/ask, spread and the contract's session range when available;
    it is never an invented percentage discount.
    """

    @staticmethod
    def _f(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def evaluate(self, contract: dict, snapshot: dict | None = None, *, horizon: str = "WEEKLY") -> EntryPlan:
        snapshot = snapshot or {}
        bid = self._f(contract.get("bid"), 0.0)
        ask = self._f(contract.get("ask"), 0.0)
        mid = self._f(contract.get("mid"), (bid + ask) / 2.0 if ask > bid > 0 else 0.0)
        spread = max(0.0, ask - bid)
        spread_pct = self._f(contract.get("spread_pct"), 99.0)
        contract_score = self._f(contract.get("contract_score"), 0.0)
        quote_age = self._f(contract.get("quote_age_minutes"), 999.0)
        theta = abs(self._f(contract.get("theta"), 0.0)) if contract.get("theta") is not None else 0.0

        daily = snapshot.get("dailyBar") or snapshot.get("daily_bar") or {}
        day_low = self._f(daily.get("l", daily.get("low")), 0.0)
        day_high = self._f(daily.get("h", daily.get("high")), 0.0)
        day_open = self._f(daily.get("o", daily.get("open")), 0.0)
        day_volume = self._f(daily.get("v", daily.get("volume")), 0.0)

        range_position = None
        range_size = max(0.0, day_high - day_low)
        if range_size > 0 and day_low > 0:
            range_position = max(0.0, min(1.0, (mid - day_low) / range_size))

        spread_fit = max(0.0, 100.0 - spread_pct * 6.0)
        freshness_fit = max(0.0, 100.0 - quote_age * 2.0)
        if range_position is None:
            position_fit = 76.0
        elif range_position <= 0.55:
            position_fit = 100.0
        elif range_position <= 0.75:
            position_fit = 100.0 - (range_position - 0.55) * 120.0
        else:
            position_fit = max(20.0, 76.0 - (range_position - 0.75) * 220.0)
        theta_fit = max(35.0, 100.0 - theta * (45.0 if str(horizon).upper() == "DAILY" else 28.0))
        entry_quality = max(0.0, min(100.0,
            0.36 * contract_score + 0.27 * spread_fit + 0.22 * position_fit + 0.10 * freshness_fit + 0.05 * theta_fit
        ))

        chase_risk = bool(range_position is not None and range_position >= 0.78)
        if spread_pct >= 8.0:
            chase_risk = True

        # Preferred price is anchored inside the executable bid/ask. If the
        # premium sits near the session high, require a modest pullback tied to
        # the observed option range; otherwise target a near-mid fill.
        target = mid
        if chase_risk and range_size > 0:
            pullback = min(range_size * 0.22, max(spread * 2.0, mid * 0.10))
            target = max(bid, mid - pullback)
        elif spread > 0:
            target = max(bid, mid - 0.15 * spread)

        half_band = max(0.01, min(max(spread * 0.30, 0.02), max(mid * 0.025, 0.05)))
        desired_low = max(0.01, target - half_band)
        desired_high = max(desired_low, min(ask if ask > 0 else target + half_band, target + half_band))

        ready_floor = 82.0 if str(horizon).upper() == "DAILY" else 80.0
        state = "READY" if entry_quality >= ready_floor and not chase_risk else "WATCH"
        if state == "READY":
            reason = "Premium is inside an efficient executable entry zone"
        elif chase_risk:
            reason = "Contract is valid but premium is extended; avoid chasing and wait for the preferred entry zone"
        else:
            reason = "Contract is valid but entry quality is below the V3 execution threshold"

        diagnostics = [
            f"contract_score={contract_score:.1f}",
            f"spread={spread_pct:.2f}%",
            f"quote_age={quote_age:.1f}m",
            f"session_range_position={range_position:.2f}" if range_position is not None else "session_range_position=UNAVAILABLE",
            f"daily_low={day_low:.2f}" if day_low > 0 else "daily_low=UNAVAILABLE",
            f"daily_high={day_high:.2f}" if day_high > 0 else "daily_high=UNAVAILABLE",
            f"daily_open={day_open:.2f}" if day_open > 0 else "daily_open=UNAVAILABLE",
            f"daily_volume={day_volume:.0f}" if day_volume > 0 else "daily_volume=UNAVAILABLE",
        ]
        return EntryPlan(
            state=state,
            entry_low=round(desired_low, 2),
            entry_high=round(desired_high, 2),
            current_price=round(mid, 2),
            entry_quality=round(entry_quality, 1),
            chase_risk=chase_risk,
            reason=reason,
            diagnostics=diagnostics,
        )
