from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from app.config import settings


@dataclass(frozen=True)
class MarketGateDecision:
    state: str
    required_score: float
    blocked: bool
    reason: str
    risk_cap: float
    liquidity_state: str
    volatility_state: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DynamicMarketGate:
    """Symmetric CALL/PUT market-quality gate.

    Direction is produced by the strategy engine. This gate never decides CALL
    versus PUT; it only decides whether the environment is tradable and how
    selective the final score threshold must be.
    """

    @staticmethod
    def _f(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def evaluate(self, analysis: dict | None, contract: dict | None = None) -> MarketGateDecision:
        a = analysis or {}
        c = contract or {}
        direction = str(a.get("direction", "NEUTRAL")).upper()
        regime = str(a.get("market_regime", "UNKNOWN")).upper()
        adx = self._f(a.get("adx"), 0.0)
        rvol = self._f(a.get("rvol"), 0.0)
        atr_pct = self._f(a.get("atr_pct"), 0.0)
        atr_ratio = self._f(a.get("atr_regime_ratio"), 1.0)
        gap = self._f(a.get("directional_gap"), 0.0)
        trend_active = bool(a.get("trend_active", direction in {"LONG", "SHORT"}))
        flags = {str(x).upper() for x in (a.get("quality_flags") or [])}
        scores = a.get("scores") or {}
        volatility_quality = self._f(scores.get("Volatility"), 65.0)

        # V20 has its own independent CALL/PUT framework. Its MTF agreement is
        # the directional clarity proxy when the Core directional_gap is absent.
        v20 = a.get("v20") or {}
        if str(a.get("strategy_id", "")).upper() == "SPX_V20" or v20:
            bull_tf = int(self._f(v20.get("bull_tf"), 0.0))
            bear_tf = int(self._f(v20.get("bear_tf"), 0.0))
            gap = float(abs(bull_tf - bear_tf) * 5)
            trend_active = direction in {"LONG", "SHORT"} and max(bull_tf, bear_tf) >= 3

        direction_clear = direction in {"LONG", "SHORT"} and trend_active and (
            gap >= settings.dynamic_min_directional_gap or adx >= settings.strong_adx
        )

        # Underlying participation state. Missing RVOL is treated as unknown,
        # not automatically as low liquidity.
        if rvol > 0 and rvol < settings.dynamic_low_liquidity_rvol:
            liquidity_state = "LOW"
        elif rvol >= settings.dynamic_high_liquidity_rvol:
            liquidity_state = "HIGH"
        else:
            liquidity_state = "NORMAL"

        # Contract execution quality can override the underlying. A technically
        # excellent thesis is not actionable through a very wide option market.
        if c:
            spread = self._f(c.get("spread_pct"), 999.0)
            contract_score = self._f(c.get("contract_score"), 0.0)
            if spread >= settings.dynamic_contract_no_trade_spread_pct:
                return MarketGateDecision(
                    "LOW_LIQUIDITY_CONTRACT", 100.0, True,
                    f"Option spread too wide ({spread:.2f}%)",
                    0.0, "LOW", "NORMAL",
                )
            if contract_score and contract_score < settings.dynamic_contract_min_quality_score:
                return MarketGateDecision(
                    "LOW_QUALITY_CONTRACT", 100.0, True,
                    f"Contract quality too low ({contract_score:.1f})",
                    0.0, "LOW", "NORMAL",
                )
            if spread <= settings.dynamic_contract_high_liquidity_spread_pct and contract_score >= 80:
                if liquidity_state != "LOW":
                    liquidity_state = "HIGH"

        if flags.intersection({"HARD_NO_TRADE", "NO_TRADE", "CHASE"}):
            return MarketGateDecision(
                "NO_TRADE", 100.0, True,
                "Strategy hard veto / chase protection",
                0.0, liquidity_state, "UNSAFE",
            )

        high_vol = (
            atr_ratio >= settings.dynamic_high_vol_atr_ratio
            or (atr_pct >= settings.dynamic_high_vol_atr_pct and volatility_quality <= 60)
        )
        if high_vol:
            volatility_state = "HIGH"
        elif atr_ratio > 0 and atr_ratio < settings.dynamic_low_vol_atr_ratio:
            volatility_state = "LOW"
        else:
            volatility_state = "NORMAL"

        range_or_mixed = "RANGE" in regime or "MIXED" in regime
        uncertain = not direction_clear or adx < settings.min_adx_trend

        if liquidity_state == "LOW" and uncertain:
            return MarketGateDecision(
                "LOW_LIQUIDITY_UNCLEAR", 100.0, True,
                "Low participation with unclear direction",
                0.0, liquidity_state, volatility_state,
            )

        # Highest-risk tradable states first. The score floor never falls below
        # settings.ready_score_floor (hard minimum 90).
        if high_vol:
            return MarketGateDecision(
                "HIGH_VOLATILITY",
                max(settings.ready_score_floor, settings.dynamic_high_vol_min_score),
                False,
                "High volatility: require stronger confirmation",
                settings.dynamic_high_vol_risk_cap,
                liquidity_state,
                volatility_state,
            )

        if liquidity_state == "LOW":
            return MarketGateDecision(
                "LOW_LIQUIDITY_CLEAR",
                max(settings.ready_score_floor, settings.dynamic_low_liquidity_min_score),
                False,
                "Direction is clear but participation is weak",
                settings.dynamic_low_liquidity_risk_cap,
                liquidity_state,
                volatility_state,
            )

        if range_or_mixed:
            return MarketGateDecision(
                "RANGE_MIXED",
                max(settings.ready_score_floor, settings.dynamic_range_min_score),
                False,
                "Range/mixed market: raise selectivity",
                settings.dynamic_range_risk_cap,
                liquidity_state,
                volatility_state,
            )

        counter_trend = (direction == "LONG" and regime == "BEAR") or (direction == "SHORT" and regime == "BULL")
        if counter_trend:
            return MarketGateDecision(
                "COUNTER_TREND",
                max(settings.ready_score_floor, settings.dynamic_countertrend_min_score),
                False,
                "Signal opposes the broad market regime",
                settings.dynamic_countertrend_risk_cap,
                liquidity_state,
                volatility_state,
            )

        if uncertain or adx < settings.strong_adx or (rvol > 0 and rvol < 1.0):
            return MarketGateDecision(
                "CAUTION",
                max(settings.ready_score_floor, settings.dynamic_caution_min_score),
                False,
                "Direction/participation is acceptable but not fully confirmed",
                settings.dynamic_caution_risk_cap,
                liquidity_state,
                volatility_state,
            )

        healthy = direction_clear and adx >= settings.strong_adx and (
            rvol == 0 or rvol >= settings.min_rvol_breakout
        )
        if healthy:
            return MarketGateDecision(
                "HEALTHY_TREND",
                settings.ready_score_floor,
                False,
                "Clear direction with healthy participation",
                settings.max_risk_per_trade,
                liquidity_state,
                volatility_state,
            )

        return MarketGateDecision(
            "NORMAL",
            settings.ready_score_floor,
            False,
            "Normal tradable conditions",
            settings.dynamic_normal_risk_cap,
            liquidity_state,
            volatility_state,
        )
