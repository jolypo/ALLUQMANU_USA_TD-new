from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.market.quality import freshness_info, validate_bars
from app.options.selector import ContractSelector
from app.trading.service import SignalService
from app.models.domain import TradeType


def _bars(last_ts: datetime, n: int = 240) -> pd.DataFrame:
    idx = pd.date_range(end=last_ts, periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({
        "timestamp": idx,
        "open": [100.0] * n,
        "high": [101.0] * n,
        "low": [99.0] * n,
        "close": [100.5] * n,
        "volume": [1000.0] * n,
    })


def test_intraday_bars_reject_previous_session():
    ny = ZoneInfo("America/New_York")
    now = datetime.now(ny)
    previous = (now - timedelta(days=1)).replace(hour=15, minute=45, second=0, microsecond=0).astimezone(timezone.utc)
    ok, reason = validate_bars(
        _bars(previous), 60,
        max_age_minutes=20,
        require_same_ny_date=True,
        now=now.astimezone(timezone.utc),
    )
    assert not ok
    assert "STALE" in reason or "جلسة سابقة" in reason


def test_missing_timestamp_is_not_fresh():
    ok, reason, age, iso = freshness_info(None, max_age_minutes=20, require_same_ny_date=True)
    assert not ok
    assert age is None and iso is None
    assert "غير متوفر" in reason


def test_option_selector_rejects_stale_quote():
    ny = ZoneInfo("America/New_York")
    expiry = (datetime.now(ny).date() + timedelta(days=7)).strftime("%y%m%d")
    sym = f"AAPL{expiry}C00200000"
    stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    payload = {"snapshots": {sym: {
        "latestQuote": {"bp": 4.80, "ap": 5.00, "t": stale},
        "greeks": {"delta": 0.55, "theta": -0.05},
        "dailyBar": {"v": 500},
    }}}
    assert ContractSelector().select(
        payload, "LONG", "AAPL", 200.0, min_dte=0, max_dte=30
    ) is None


class _History:
    def all(self):
        return []


class _StaleStockProvider:
    async def latest_bars(self, symbols):
        stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        return {s: {"c": 100.0, "t": stale} for s in symbols}


@pytest.mark.asyncio
async def test_stock_candidates_reject_stale_current_snapshot(monkeypatch):
    service = SignalService(_StaleStockProvider(), _History())
    monkeypatch.setattr("app.trading.service.settings.stock_symbols", "AAPL")
    async def no_benchmark(_): return None
    async def no_news(_): return {"modifier": 0.0, "severe_negative": False, "headline": None}
    monkeypatch.setattr(service, "_benchmark_return", no_benchmark)
    monkeypatch.setattr(service, "_news_context", no_news)
    candidates, rejects = await service._stock_candidates([TradeType.STOCK_INTRADAY])
    assert candidates == []
    assert any("STALE/INVALID current market data" in x for x in rejects)
