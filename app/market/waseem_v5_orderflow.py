from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import defaultdict, deque
from datetime import datetime, timezone
import math


@dataclass
class V5OrderFlowResult:
    score: float
    bid_ask_pressure_score: float | None
    trade_aggression_score: float | None
    execution_pressure_score: float | None
    book_imbalance_score: float | None
    absorption_score: float | None
    replenishment_score: float | None
    flow_confidence: str
    quote_status: str
    trade_status: str
    samples: int
    diagnostics: list[str]

    def to_dict(self):
        return asdict(self)


class WaseemV5OrderFlowEngine:
    """Top-of-book/order-flow layer for Waseem V5.

    This engine deliberately does NOT invent Level-2/DOM fields. With the
    current Alpaca snapshots it can use best bid/ask, sizes when supplied,
    latest trade and changes observed across scans. Multi-level depth,
    institutional flow, sweeps and blocks remain UNAVAILABLE unless a future
    provider adapter supplies those fields explicitly.
    """

    def __init__(self, max_samples: int = 24):
        self._history = defaultdict(lambda: deque(maxlen=max_samples))

    @staticmethod
    def _f(value, default=None):
        try:
            v = float(value)
            return v if math.isfinite(v) else default
        except Exception:
            return default

    @staticmethod
    def _pick(d: dict, *names):
        for name in names:
            if name in d and d.get(name) is not None:
                return d.get(name)
        return None

    def evaluate(self, contract_symbol: str, snapshot: dict | None, direction: str) -> V5OrderFlowResult:
        snap = snapshot or {}
        q = snap.get("latestQuote") or snap.get("latest_quote") or {}
        tr = snap.get("latestTrade") or snap.get("latest_trade") or {}
        bid = self._f(self._pick(q, "bp", "bid_price", "bidPrice"))
        ask = self._f(self._pick(q, "ap", "ask_price", "askPrice"))
        bid_size = self._f(self._pick(q, "bs", "bid_size", "bidSize"))
        ask_size = self._f(self._pick(q, "as", "ask_size", "askSize"))
        trade_price = self._f(self._pick(tr, "p", "price"))
        trade_size = self._f(self._pick(tr, "s", "size"))
        quote_ts = self._pick(q, "t", "timestamp", "time")
        trade_ts = self._pick(tr, "t", "timestamp", "time")
        long = str(direction).upper() in {"LONG", "CALL", "BUY"}
        diagnostics: list[str] = []

        quote_status = "AVAILABLE" if bid is not None and ask is not None and ask >= bid > 0 else "UNAVAILABLE"
        trade_status = "AVAILABLE" if trade_price is not None and trade_price > 0 else "UNAVAILABLE"

        pressure = None
        if bid_size is not None and ask_size is not None and bid_size + ask_size > 0:
            imbalance = (bid_size - ask_size) / (bid_size + ask_size)
            signed = imbalance if long else -imbalance
            pressure = max(0.0, min(100.0, 50.0 + 50.0 * signed))
            diagnostics.append(f"top_book_size_imbalance={imbalance:+.3f}")
        elif bid is not None and ask is not None:
            pressure = 50.0
            diagnostics.append("top_book_sizes=UNAVAILABLE")
        else:
            diagnostics.append("bid_ask_pressure=UNAVAILABLE")

        aggression = None
        if trade_price is not None and bid is not None and ask is not None and ask > bid:
            pos = (trade_price - bid) / max(ask - bid, 1e-9)
            pos = max(0.0, min(1.0, pos))
            aggression = 100.0 * (pos if long else 1.0 - pos)
            diagnostics.append(f"latest_trade_vs_quote={pos:.2f}")
        else:
            diagnostics.append("trade_aggression=UNAVAILABLE")

        key = str(contract_symbol or "UNKNOWN").upper()
        hist = self._history[key]
        execution = None
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
            previous = hist[-1] if hist else None
            if previous and previous.get("mid") is not None:
                delta_mid = mid - previous["mid"]
                signed_delta = delta_mid if long else -delta_mid
                spread = max(ask - bid, 0.01)
                execution = max(0.0, min(100.0, 50.0 + (signed_delta / spread) * 25.0))
                diagnostics.append(f"mid_change={delta_mid:+.4f}")
            else:
                execution = 50.0
                diagnostics.append("execution_pressure=BASELINE_FIRST_SAMPLE")
            hist.append({
                "at": datetime.now(timezone.utc).isoformat(), "bid": bid, "ask": ask,
                "bid_size": bid_size, "ask_size": ask_size, "trade": trade_price,
                "trade_size": trade_size, "mid": mid,
            })

        usable = [x for x in (pressure, aggression, execution) if x is not None]
        if not usable:
            score = 50.0
            confidence = "UNAVAILABLE"
        else:
            weights = []
            if pressure is not None: weights.append((pressure, 0.35))
            if aggression is not None: weights.append((aggression, 0.40))
            if execution is not None: weights.append((execution, 0.25))
            total_w = sum(w for _, w in weights)
            score = sum(v*w for v, w in weights) / total_w
            if quote_status == "AVAILABLE" and trade_status == "AVAILABLE" and bid_size is not None and ask_size is not None:
                confidence = "HIGH" if len(hist) >= 3 else "MEDIUM"
            elif quote_status == "AVAILABLE":
                confidence = "MEDIUM" if trade_status == "AVAILABLE" else "LOW"
            else:
                confidence = "LOW"

        diagnostics.extend([
            "book_depth_imbalance=UNAVAILABLE (no multi-level depth feed)",
            "absorption=UNAVAILABLE (requires depth/tick sequence)",
            "replenishment=UNAVAILABLE (requires depth updates)",
            f"flow_confidence={confidence}",
            f"quote_timestamp={quote_ts or 'UNAVAILABLE'}",
            f"trade_timestamp={trade_ts or 'UNAVAILABLE'}",
        ])
        return V5OrderFlowResult(
            round(score, 1), None if pressure is None else round(pressure, 1),
            None if aggression is None else round(aggression, 1),
            None if execution is None else round(execution, 1),
            None, None, None, confidence, quote_status, trade_status, len(hist), diagnostics,
        )
