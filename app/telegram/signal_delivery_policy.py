from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Iterable, Any


@dataclass(frozen=True)
class DeliveryDecision:
    allowed: bool
    reason: str
    remaining_seconds: float = 0.0
    material_override: bool = False


class SignalDeliveryPolicy:
    """Telegram delivery policy only; never changes strategy decisions.

    The trading engines remain free to scan/re-evaluate every cycle.  This layer
    only decides which *new opportunity messages* deserve to be delivered so one
    underlying cannot monopolize the Telegram feed.
    """

    def __init__(self, *, cooldown_seconds: int = 1200, upgrade_score_delta: float = 3.0):
        self.cooldown_seconds = max(0, int(cooldown_seconds))
        self.upgrade_score_delta = max(0.0, float(upgrade_score_delta))
        self._last_by_symbol: dict[str, dict[str, Any]] = {}
        self.suppressed: list[dict[str, Any]] = []

    @staticmethod
    def symbol(trade: dict) -> str:
        return str(trade.get("symbol") or "").strip().upper()

    @staticmethod
    def score(trade: dict) -> float:
        try:
            return float(trade.get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def required_score(trade: dict) -> float:
        try:
            return float(trade.get("required_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def decision(trade: dict) -> str:
        return str(trade.get("decision") or "READY").strip().upper()

    @staticmethod
    def direction(trade: dict) -> str:
        option = trade.get("option") or {}
        raw = str(
            option.get("underlying_direction")
            or option.get("option_type")
            or option.get("type")
            or trade.get("direction")
            or ""
        ).strip().upper()
        if raw in {"CALL", "LONG", "BULLISH", "BUY"}:
            return "CALL"
        if raw in {"PUT", "SHORT", "BEARISH", "SELL"}:
            return "PUT"
        return raw or "UNKNOWN"

    @staticmethod
    def engine(trade: dict) -> str:
        option = trade.get("option") or {}
        return str(option.get("strategy_mode") or trade.get("engine_source") or "CORE").strip().upper()

    @staticmethod
    def contract_key(trade: dict) -> str:
        option = trade.get("option") or {}
        return str(
            option.get("symbol")
            or f"{trade.get('symbol','')}|{option.get('strike','')}|{option.get('type','')}|{option.get('expiration','')}"
        ).strip().upper()

    @staticmethod
    def _watch_to_ready(trade: dict) -> bool:
        return any(
            str(trade.get(k) or "").upper() == "WATCH_TO_READY"
            for k in ("v4_watch_transition", "v5_watch_transition", "v6_watch_transition")
        )

    def select_unique_symbols(self, candidates: Iterable[Any], max_results: int) -> tuple[list[Any], list[dict]]:
        """Return the strongest candidate per underlying, preserving score quality.

        This is deliberately downstream of every engine.  It changes neither an
        engine score nor a WATCH/READY decision; it only prevents multiple option
        contracts for the same underlying from consuming all Telegram slots.
        """
        best: dict[str, tuple[float, int, Any, dict]] = {}
        rejected: list[dict] = []
        for idx, row in enumerate(candidates):
            trade = row.to_dict() if hasattr(row, "to_dict") else dict(row)
            sym = self.symbol(trade) or f"__UNKNOWN_{idx}"
            score = self.score(trade)
            current = best.get(sym)
            if current is None or score > current[0]:
                if current is not None:
                    rejected.append({"symbol": sym, "reason": "DUPLICATE_SYMBOL_LOWER_SCORE", "score": current[0]})
                best[sym] = (score, idx, row, trade)
            else:
                rejected.append({"symbol": sym, "reason": "DUPLICATE_SYMBOL_LOWER_SCORE", "score": score})

        rows = sorted(best.values(), key=lambda x: (x[0], -x[1]), reverse=True)
        selected = [x[2] for x in rows[: max(0, int(max_results))]]
        for x in rows[max(0, int(max_results)):]:
            rejected.append({"symbol": self.symbol(x[3]), "reason": "UNIQUE_SYMBOL_RANK_BELOW_CUTOFF", "score": x[0]})
        return selected, rejected

    def evaluate(self, trade: dict, *, now: float | None = None) -> DeliveryDecision:
        now = time.monotonic() if now is None else float(now)
        sym = self.symbol(trade)
        if not sym or self.cooldown_seconds <= 0:
            return DeliveryDecision(True, "NO_SYMBOL_OR_COOLDOWN_DISABLED")

        prev = self._last_by_symbol.get(sym)
        if not prev:
            return DeliveryDecision(True, "FIRST_SYMBOL_SIGNAL")

        elapsed = max(0.0, now - float(prev.get("sent_at", 0.0)))
        if elapsed >= self.cooldown_seconds:
            return DeliveryDecision(True, "SYMBOL_COOLDOWN_EXPIRED")

        current_decision = self.decision(trade)
        current_direction = self.direction(trade)
        current_score = self.score(trade)
        previous_decision = str(prev.get("decision") or "READY")
        previous_direction = str(prev.get("direction") or "UNKNOWN")
        previous_score = float(prev.get("score") or 0.0)

        # Material state changes are important enough to bypass anti-spam.
        if self._watch_to_ready(trade) or (previous_decision == "WATCH" and current_decision == "READY"):
            return DeliveryDecision(True, "WATCH_TO_READY_OVERRIDE", material_override=True)

        # A confirmed directional reversal is a new thesis, not a duplicate.
        if current_direction in {"CALL", "PUT"} and previous_direction in {"CALL", "PUT"} and current_direction != previous_direction:
            floor = max(88.0, self.required_score(trade))
            if current_score >= floor and current_decision == "READY":
                return DeliveryDecision(True, "CONFIRMED_DIRECTION_REVERSAL_OVERRIDE", material_override=True)

        # A materially stronger READY setup can supersede an earlier, weaker one.
        if current_decision == "READY" and current_score >= previous_score + self.upgrade_score_delta:
            return DeliveryDecision(True, "MATERIAL_SCORE_UPGRADE_OVERRIDE", material_override=True)

        remaining = max(0.0, self.cooldown_seconds - elapsed)
        return DeliveryDecision(False, "GLOBAL_SYMBOL_COOLDOWN", remaining_seconds=remaining)

    def record_sent(self, trade: dict, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else float(now)
        sym = self.symbol(trade)
        if not sym:
            return
        self._last_by_symbol[sym] = {
            "sent_at": now,
            "score": self.score(trade),
            "decision": self.decision(trade),
            "direction": self.direction(trade),
            "engine": self.engine(trade),
            "contract_key": self.contract_key(trade),
        }

    def record_suppressed(self, trade: dict, *, reason: str, extra: dict | None = None) -> None:
        row = {
            "symbol": self.symbol(trade),
            "score": self.score(trade),
            "decision": self.decision(trade),
            "direction": self.direction(trade),
            "engine": self.engine(trade),
            "reason": str(reason),
        }
        if extra:
            row.update(extra)
        self.suppressed.append(row)
        if len(self.suppressed) > 200:
            del self.suppressed[:-200]
