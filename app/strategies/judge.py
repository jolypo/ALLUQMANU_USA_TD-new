from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.config import settings
from app.models.domain import Signal


@dataclass(frozen=True)
class JudgeResult:
    score: float
    rank: int
    decision: str
    reasons: list[str]


class JudgeEngine:
    """Second-stage independent ranking layer for Confirmed Setup candidates.

    v6 adds a conservative learning adjustment. The raw Judge score must still
    be >= 90 before learning is even considered, so learning can never bypass
    the original hard floor or the existing market/contract gates.
    """

    def __init__(self, learning_store=None):
        self.learning = learning_store

    @staticmethod
    def _spread_pct(signal: Signal) -> float | None:
        option = signal.option or {}
        try:
            bid = float(option.get("bid"))
            ask = float(option.get("ask"))
            mid = (bid + ask) / 2.0
            if bid <= 0 or ask <= bid or mid <= 0:
                return None
            return (ask - bid) / mid * 100.0
        except (TypeError, ValueError):
            return None

    def _raw_score(self, signal: Signal) -> tuple[float, list[str]]:
        option = signal.option or {}
        contract = float(option.get("contract_score", 0.0) or 0.0)
        direction = float(signal.score)

        market_bonus = {
            "HEALTHY": 5.0,
            "NORMAL": 2.0,
            "CAUTION": -2.0,
            "RANGE": -4.0,
            "COUNTER_TREND": -5.0,
            "HIGH_VOLATILITY": -5.0,
            "LOW_LIQUIDITY": -7.0,
        }.get(str(signal.market_state or "NORMAL").upper(), 0.0)

        liquidity_bonus = {
            "HIGH": 4.0,
            "NORMAL": 1.0,
            "LOW": -6.0,
        }.get(str(signal.liquidity_state or "NORMAL").upper(), 0.0)

        spread = self._spread_pct(signal)
        execution = 70.0
        reasons: list[str] = []
        if spread is not None:
            if spread <= 2.0:
                execution = 100.0
                reasons.append("Spread ممتاز")
            elif spread <= 4.0:
                execution = 90.0
                reasons.append("Spread جيد")
            elif spread <= 6.0:
                execution = 78.0
            else:
                execution = 60.0
                reasons.append("Spread يحتاج حذر")

        raw = 0.52 * direction + 0.30 * contract + 0.18 * execution + market_bonus + liquidity_bonus
        return round(max(0.0, min(100.0, raw)), 1), reasons

    def score(self, signal: Signal) -> tuple[float, list[str]]:
        raw, reasons = self._raw_score(signal)

        # Safety: no learned bonus may rescue a candidate that baseline Judge rejects.
        if raw < 90.0:
            reasons.append("Judge: أقل من معيار الاعتماد")
            return raw, reasons

        final = raw
        learned = None
        if settings.learning_enabled and self.learning is not None:
            learned = self.learning.adjustment_for_signal(signal)
            if learned.status == "ACTIVE":
                final = round(max(0.0, min(100.0, raw + learned.adjustment)), 1)
                reasons.append(
                    f"Learning {learned.adjustment:+.2f} | {learned.samples} samples | {learned.source}"
                )
            else:
                reasons.append(
                    f"Learning: collecting {learned.samples}/{settings.learning_min_global_samples}"
                )

        if final >= 92:
            reasons.append("Judge: ممتاز")
        elif final >= 90:
            reasons.append("Judge: قوي")
        else:
            reasons.append("Judge: التعلم التاريخي خفّض الاعتماد")

        option = signal.option if signal.option is not None else {}
        option["judge_raw_score"] = raw
        option["judge_learning_adjustment"] = round(final - raw, 2)
        option["learning_status"] = learned.status if learned else "DISABLED"
        option["learning_samples"] = learned.samples if learned else 0
        option["learning_win_rate"] = learned.win_rate if learned else None
        signal.option = option
        return final, reasons

    def rank(self, candidates: Iterable[Signal], max_results: int = 3) -> list[Signal]:
        scored: list[tuple[float, Signal, list[str]]] = []
        for signal in candidates:
            value, reasons = self.score(signal)
            if value < 90.0:
                continue
            scored.append((value, signal, reasons))
        scored.sort(key=lambda row: (row[0], row[1].score), reverse=True)

        selected: list[Signal] = []
        used_exposures: set[tuple[str, str]] = set()
        for value, signal, reasons in scored:
            underlying_direction = str((signal.option or {}).get("underlying_direction") or signal.direction or "")
            exposure = (str(signal.sector or "N/A"), underlying_direction)
            is_index = str(signal.trade_type.value).startswith("INDEX_OPTION_")
            if not is_index and exposure[0] != "N/A" and exposure in used_exposures:
                continue
            used_exposures.add(exposure)

            option = signal.option if signal.option is not None else {}
            option["strategy_mode"] = "CONFIRMED_SETUP"
            option["judge_score"] = value
            option["judge_rank"] = len(selected) + 1
            option["judge_decision"] = "APPROVE"
            signal.option = option
            signal.reasons = list(signal.reasons) + reasons

            ctx = dict(signal.market_context or {})
            ctx["judge_score"] = value
            ctx["judge_raw_score"] = option.get("judge_raw_score", value)
            ctx["judge_learning_adjustment"] = option.get("judge_learning_adjustment", 0.0)
            ctx["learning_status"] = option.get("learning_status", "DISABLED")
            ctx["learning_samples"] = option.get("learning_samples", 0)
            ctx["judge_rank"] = len(selected) + 1
            ctx["judge_decision"] = "APPROVE"
            signal.market_context = ctx
            selected.append(signal)
            if len(selected) >= max_results:
                break
        return selected
