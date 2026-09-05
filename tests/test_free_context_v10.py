import csv
import io
from pathlib import Path

import pytest

from app.config import settings
from app.providers.economic import EconomicContextProvider
from app.telegram.messages import signal_caption


class DummyResponse:
    def __init__(self, *, json_data=None, text=""):
        self._json = json_data
        self.text = text
    def raise_for_status(self):
        return None
    def json(self):
        return self._json


@pytest.mark.asyncio
async def test_alpha_vantage_calendar_is_one_cached_fetch(monkeypatch):
    provider = EconomicContextProvider()
    monkeypatch.setattr(settings, "alpha_vantage_enabled", True)
    monkeypatch.setattr(settings, "alpha_vantage_api_key", "test-key")
    calls = {"n": 0}
    csv_text = "symbol,name,reportDate,fiscalDateEnding,estimate,currency\nAMZN,Amazon,2099-01-20,2098-12-31,1.23,USD\nMSFT,Microsoft,2099-01-21,2098-12-31,2.00,USD\n"
    async def fake_get(*args, **kwargs):
        calls["n"] += 1
        return DummyResponse(text=csv_text)
    monkeypatch.setattr(provider.client, "get", fake_get)
    try:
        a = await provider.alpha_vantage_earnings("AMZN")
        b = await provider.alpha_vantage_earnings("MSFT")
        assert calls["n"] == 1
        assert a["status"] == "AVAILABLE"
        assert a["next_earnings"]["report_date"] == "2099-01-20"
        assert b["status"] == "AVAILABLE"
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_missing_keys_are_explicit_unavailable(monkeypatch):
    provider = EconomicContextProvider()
    monkeypatch.setattr(settings, "fred_api_key", None)
    monkeypatch.setattr(settings, "alpha_vantage_api_key", None)
    try:
        fred = await provider.fred_release_calendar()
        av = await provider.alpha_vantage_earnings("AMZN")
        assert fred["status"] == "UNAVAILABLE"
        assert av["status"] == "UNAVAILABLE"
        assert fred["reason"] == "API_KEY_NOT_CONFIGURED"
        assert av["reason"] == "API_KEY_NOT_CONFIGURED"
    finally:
        await provider.close()


def test_v10_waseem_v2_only_integration_and_env_placeholders():
    service = (Path(__file__).resolve().parents[1] / "app" / "trading" / "service.py").read_text()
    env = (Path(__file__).resolve().parents[1] / ".env.example").read_text()
    assert "self.economic_context.equity_context" in service
    assert "self.economic_context.index_context" in service
    assert "EconomicContextProvider" in service
    assert "FRED_API_KEY=" in env
    assert "ALPHA_VANTAGE_API_KEY=" in env
    # No secrets are packaged in the example.
    assert "7edbdeea" not in env
    assert "H6CZV5" not in env


def test_v2_caption_prioritizes_feed_statuses():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    s = {
        "symbol": "SPX", "trade_type": "INDEX_OPTION_INTRADAY", "direction": "LONG",
        "score": 92.0, "entry_low": 3.0, "entry_high": 3.1, "stop": 2.4,
        "tp1": 4.0, "tp2": 4.8, "tp3": 5.6, "rr": 2.0, "risk_pct": .0035,
        "market_state": "WASEEM_V2_SOFT_CONTEXT", "required_score": 90,
        "liquidity_state": "HIGH", "data_quality": "LIMITED", "reasons": ["test"],
        "created_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "option": {
            "type": "CALL", "strike": 7710, "expiration": datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
            "dte": 0, "horizon": "DAILY", "mid": 3.0, "bid": 2.95, "ask": 3.05, "spread_pct": 3.3,
            "strategy_mode": "WASEEM_V2", "engine_source": "Waseem V2", "strike_efficiency": 88,
            "market_context_modifier": 2.1,
            "market_context_lines": [
                "YM: NEUTRAL", "RTY: UNAVAILABLE", "ES: BULLISH", "NQ: BULLISH", "VIX: BEARISH",
                "Economic Calendar (FRED): UNAVAILABLE — API_KEY_NOT_CONFIGURED",
            ],
            "underlying_direction": "LONG", "underlying_current_price": 7700,
            "underlying_entry_low": 7698, "underlying_entry_high": 7702, "underlying_stop": 7688,
        },
    }
    text = signal_caption(s)
    # The approved compact opportunity caption intentionally omits market-context
    # diagnostics; they remain available in READY/WATCH details.
    assert "الأحداث الاقتصادية" not in text
    assert "ES: BULLISH" not in text
    assert len(text) <= 1024
