from __future__ import annotations

from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.market.quality import freshness_info
from app.options.selector import parse_occ


class WaseemContractSelector:
    """Near-OTM adaptive selector used ONLY by Waseem V1.

    Legacy ContractSelector is intentionally untouched. This selector favors a
    tradable near-OTM contract whose distance is justified by the expected move,
    while treating missing 0DTE Greeks as a diagnostic/penalty rather than an
    automatic rejection.
    """

    @staticmethod
    def _f(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def rank(
        self,
        payload: dict,
        direction: str,
        expected_underlying: str,
        underlying_price: float,
        *,
        min_dte: int,
        max_dte: int,
        horizon: str,
        expected_move: float | None = None,
        max_contract_price: float | None = None,
        is_index: bool = False,
        max_results: int = 3,
    ) -> tuple[list[dict], list[str]]:
        snaps = payload.get("snapshots", {}) or {}
        desired = "CALL" if str(direction).upper() == "LONG" else "PUT"
        expected = str(expected_underlying or "").upper().replace("/", "")
        h = str(horizon or "WEEKLY").upper()
        diagnostics = Counter()
        ranked: list[dict] = []

        # The user explicitly wants near-OTM, not lottery-distance contracts.
        if is_index:
            max_abs_distance = 40.0
            max_distance_pct = None
        else:
            max_abs_distance = None
            max_distance_pct = {"DAILY": 3.5, "WEEKLY": 5.0, "MONTHLY": 8.0}.get(h, 5.0)

        # Spread is evaluated as quality, but truly untradable quotes are still vetoed.
        hard_spread = {"DAILY": 15.0, "WEEKLY": 12.0, "MONTHLY": 12.0}.get(h, 12.0)
        quote_age_limit = float(settings.option_quote_max_age_minutes)

        for sym, snap in snaps.items():
            meta = parse_occ(sym)
            if not meta:
                diagnostics["invalid_occ"] += 1
                continue
            if meta["type"] != desired:
                diagnostics["wrong_side"] += 1
                continue
            if meta["dte"] < int(min_dte) or meta["dte"] > int(max_dte):
                diagnostics["wrong_dte"] += 1
                continue
            root = meta["root"].replace("/", "")
            allowed = {expected, "SPXW"} if expected == "SPX" else {expected}
            if expected and root not in allowed:
                diagnostics["wrong_root"] += 1
                continue

            strike = float(meta["strike"])
            # Waseem V1 intentionally searches ATM + near OTM only.
            signed_distance = strike - float(underlying_price)
            otm_distance = signed_distance if desired == "CALL" else -signed_distance
            if otm_distance < -1e-9:  # ITM is not the target of this specific engine.
                diagnostics["itm_not_target"] += 1
                continue
            abs_distance = abs(signed_distance)
            distance_pct = abs_distance / float(underlying_price) * 100 if underlying_price > 0 else 999.0
            if max_abs_distance is not None and abs_distance > max_abs_distance:
                diagnostics["strike_too_far"] += 1
                continue
            if max_distance_pct is not None and distance_pct > max_distance_pct:
                diagnostics["strike_too_far"] += 1
                continue

            q = snap.get("latestQuote") or snap.get("latest_quote") or {}
            qts = q.get("t") or q.get("timestamp") or q.get("time")
            fresh, reason, age, iso = freshness_info(qts, max_age_minutes=quote_age_limit, require_same_ny_date=True)
            if not fresh:
                diagnostics["stale_quote"] += 1
                continue
            bid = self._f(q.get("bp", q.get("bid_price")), 0.0)
            ask = self._f(q.get("ap", q.get("ask_price")), 0.0)
            bid_size = self._f(q.get("bs", q.get("bid_size")), 0.0)
            ask_size = self._f(q.get("as", q.get("ask_size")), 0.0)
            if bid <= 0 or ask <= bid:
                diagnostics["bad_bid_ask"] += 1
                continue
            mid = (bid + ask) / 2.0
            if max_contract_price and float(max_contract_price) > 0 and ask > float(max_contract_price):
                diagnostics["price_cap"] += 1
                continue
            spread = (ask - bid) / mid * 100 if mid > 0 else 999.0
            if spread > hard_spread:
                diagnostics["spread_too_wide"] += 1
                continue

            greeks = snap.get("greeks") or {}
            delta = greeks.get("delta")
            ad = abs(self._f(delta, -1.0)) if delta is not None else None
            # For 0DTE, missing Greeks are allowed because the feed may not provide
            # reliable values. Implausible values still receive a quality penalty.
            if h != "DAILY" and ad is None:
                diagnostics["missing_delta"] += 1
                continue
            if h != "DAILY" and ad is not None and not (0.20 <= ad <= 0.85):
                diagnostics["delta_outside_broad_band"] += 1
                continue

            daily = snap.get("dailyBar") or snap.get("daily_bar") or {}
            volume = self._f(daily.get("v", daily.get("volume")), 0.0)
            iv = snap.get("impliedVolatility") or snap.get("implied_volatility")
            theta = greeks.get("theta")

            # Expected move fit: strike can be beyond the expected target because
            # the option may be sold before the strike is touched, but distance
            # should remain proportionate to the expected move.
            em = max(self._f(expected_move, 0.0), 0.0)
            if em > 0:
                coverage = abs_distance / em
                if coverage <= 0.50:
                    move_score = 100.0
                elif coverage <= 1.0:
                    move_score = 95.0 - (coverage - 0.5) * 30.0
                elif coverage <= 1.35:
                    move_score = 80.0 - (coverage - 1.0) * 50.0
                else:
                    move_score = max(25.0, 62.5 - (coverage - 1.35) * 45.0)
            else:
                coverage = None
                move_score = max(40.0, 100.0 - distance_pct * 10.0)

            spread_score = max(0.0, 100.0 - spread * 5.0)
            liquidity_score = 55.0
            if volume > 0:
                liquidity_score += min(25.0, 4.0 * (volume ** 0.25))
            if bid_size > 0 and ask_size > 0:
                liquidity_score += min(20.0, (min(bid_size, ask_size) ** 0.5) * 3.0)
            liquidity_score = min(100.0, liquidity_score)

            # Premium quality rewards affordable contracts without making cheapness
            # a trade signal by itself.
            premium_score = 80.0
            if ask <= 1.0:
                premium_score = 62.0
            elif ask <= 3.0:
                premium_score = 92.0
            elif ask <= 8.0:
                premium_score = 88.0
            elif ask <= 15.0:
                premium_score = 78.0
            else:
                premium_score = 68.0

            greek_score = 75.0
            if ad is not None:
                greek_score = max(45.0, 100.0 - abs(ad - 0.50) * 90.0)
            elif h == "DAILY":
                greek_score = 70.0

            score = (
                0.28 * move_score
                + 0.25 * spread_score
                + 0.20 * liquidity_score
                + 0.17 * premium_score
                + 0.10 * greek_score
            )
            score = max(0.0, min(100.0, score))
            if score < 65.0:
                diagnostics["waseem_contract_score_low"] += 1
                continue

            ranked.append({
                "symbol": sym,
                **meta,
                "bid": round(bid, 2), "ask": round(ask, 2), "mid": round(mid, 2),
                "bid_size": bid_size, "ask_size": ask_size,
                "spread_pct": round(spread, 2),
                "delta": float(delta) if delta is not None else None,
                "gamma": greeks.get("gamma"), "theta": theta,
                "vega": greeks.get("vega"), "rho": greeks.get("rho"), "iv": iv,
                "volume": volume,
                "strike_distance": round(abs_distance, 2),
                "strike_distance_pct": round(distance_pct, 2),
                "expected_move": round(em, 2) if em > 0 else None,
                "expected_move_coverage": round(coverage, 2) if coverage is not None else None,
                "contract_score": round(score, 1),
                "quote_timestamp": iso,
                "quote_age_minutes": round(float(age or 0.0), 2),
                "quote_freshness": reason,
                "selection_engine": "WASEEM_V1",
                "selection_reason": "Near-OTM + expected move + premium + spread + liquidity",
            })

        ranked.sort(key=lambda x: (float(x.get("contract_score", 0)), -float(x.get("spread_pct", 999))), reverse=True)
        diag = [f"{k}={v}" for k, v in diagnostics.most_common()]
        if ranked:
            diag.insert(0, f"eligible={len(ranked)}")
        return ranked[:max_results], diag
