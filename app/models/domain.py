from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TradeType(str, Enum):
    STOCK_INTRADAY = "STOCK_INTRADAY"
    STOCK_SWING = "STOCK_SWING"
    EQUITY_OPTION_INTRADAY = "EQUITY_OPTION_INTRADAY"
    EQUITY_OPTION_SWING = "EQUITY_OPTION_SWING"
    INDEX_OPTION_INTRADAY = "INDEX_OPTION_INTRADAY"
    INDEX_OPTION_SWING = "INDEX_OPTION_SWING"


class Decision(str, Enum):
    READY = "READY"
    WATCH = "WATCH"
    REJECT = "REJECT"


@dataclass
class Signal:
    symbol: str
    trade_type: TradeType
    direction: str
    decision: Decision
    score: float
    entry_low: float
    entry_high: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    rr: float
    risk_pct: float
    reasons: list[str] = field(default_factory=list)
    invalidation: list[str] = field(default_factory=list)
    strategies: list[str] = field(default_factory=list)
    market_regime: str = "UNKNOWN"
    sector: str = "N/A"
    data_quality: str = "LIMITED"
    probability_status: str = "UNVALIDATED"
    probability_samples: int = 0
    probability: float | None = None
    option: dict[str, Any] | None = None
    current_price: float | None = None
    market_timestamp: str | None = None
    market_age_minutes: float | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    market_state: str = "NORMAL"
    required_score: float = 90.0
    liquidity_state: str = "NORMAL"
    volatility_state: str = "NORMAL"
    market_context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["trade_type"] = self.trade_type.value
        d["decision"] = self.decision.value
        return d
