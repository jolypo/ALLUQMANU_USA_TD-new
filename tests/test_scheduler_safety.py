from pathlib import Path

def test_scheduler_has_no_signal_creation_reference():
    text=Path('app/scheduler/monitor.py').read_text(encoding='utf-8')
    assert 'best_stock(' not in text
    assert 'best_equity_option(' not in text
    assert 'best_index_option(' not in text

import pytest
from app.scheduler.monitor import TradeMonitor


class _EntryRangeProvider:
    async def entry_price_range_since(self, symbol, start, option_contract=None):
        return (6.80, 9.75)


def _monitor(provider=None):
    return TradeMonitor(None, None, None, provider or _EntryRangeProvider(), None, None, None, None)


@pytest.mark.asyncio
async def test_entry_touch_detects_historical_cross_for_equity_option():
    trade = {
        "symbol": "NVDA",
        "trade_type": "EQUITY_OPTION_SWING",
        "direction": "LONG",
        "entry_low": 7.94,
        "entry_high": 7.95,
        "published_at": "2026-08-27T14:00:00+00:00",
        "option": {"symbol": "NVDA260918C00225000", "type": "CALL"},
    }
    m = _monitor()
    assert await m._entry_touched(trade, 9.75, None) is True
    assert m._conservative_fill(trade) == 7.95


def test_conservative_fill_all_trade_classes():
    m = _monitor()
    assert m._conservative_fill({"entry_low": 100, "entry_high": 101, "direction": "LONG"}) == 101
    assert m._conservative_fill({"entry_low": 100, "entry_high": 101, "direction": "SHORT"}) == 100
    assert m._conservative_fill({"entry_low": 7.94, "entry_high": 7.95, "direction": "LONG", "option": {"type": "PUT"}}) == 7.95


@pytest.mark.asyncio
async def test_option_success_threshold_marks_once_at_50_usd(monkeypatch):
    from app.runtime_settings import success_rules

    monkeypatch.setattr(
        success_rules,
        "get",
        lambda category: {"threshold": 50.0, "unit": "USD"},
    )
    trade = {
        "trade_id": "OPT-50",
        "symbol": "NVDA",
        "trade_type": "EQUITY_OPTION_INTRADAY",
        "direction": "LONG",
        "filled_entry_price": 1.00,
        "entry_low": 1.00,
        "entry_high": 1.00,
        "status": "OPEN",
        "contracts": 1,
        "option": {"symbol": "NVDA260918C00225000", "type": "CALL"},
    }
    m = _monitor()
    assert await m._mark_success_if_reached(trade, 1.50) is True
    assert trade["success_reached"] is True
    assert trade["success_threshold_value"] == 50.0
    assert trade["success_threshold_unit"] == "USD"
    assert await m._mark_success_if_reached(trade, 2.00) is False


def test_option_performance_loss_waits_until_new_york_close(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.runtime_settings import success_rules

    monkeypatch.setattr(success_rules, "get", lambda category: {"threshold": 50.0, "unit": "USD"})
    trade = {
        "trade_id": "OPT-EOD",
        "symbol": "SPX",
        "trade_type": "INDEX_OPTION_INTRADAY",
        "status": "OPEN",
        "entry_confirmed": True,
        "filled_entry_price": 2.00,
        "entered_at": "2026-08-27T18:00:00+00:00",  # 14:00 New York
        "max_profit_usd": 40.0,
        "contracts": 1,
        "option": {"type": "CALL", "strike": 6500},
    }
    m = _monitor()
    rows, changed = m._finalize_option_performance_rows(
        [dict(trade)], datetime(2026, 8, 27, 15, 59, tzinfo=ZoneInfo("America/New_York"))
    )
    assert changed is False
    assert rows[0].get("performance_result") is None

    rows, changed = m._finalize_option_performance_rows(
        [dict(trade)], datetime(2026, 8, 27, 16, 1, tzinfo=ZoneInfo("America/New_York"))
    )
    assert changed is True
    assert rows[0]["performance_result"] == "LOSS"
    assert rows[0]["performance_loss_reason"] == "THRESHOLD_NOT_REACHED_BY_EXPIRY"


def test_option_success_freezes_before_market_close(monkeypatch):
    from app.runtime_settings import success_rules

    monkeypatch.setattr(success_rules, "get", lambda category: {"threshold": 50.0, "unit": "USD"})
    trade = {
        "trade_id": "OPT-WIN",
        "symbol": "NVDA",
        "trade_type": "EQUITY_OPTION_INTRADAY",
        "status": "OPEN",
        "entry_confirmed": True,
        "filled_entry_price": 1.00,
        "entered_at": "2026-08-27T18:00:00+00:00",
        "contracts": 1,
        "option": {"type": "CALL", "strike": 200},
    }
    m = _monitor()
    import asyncio
    assert asyncio.run(m._mark_success_if_reached(trade, 1.50)) is True
    assert trade["performance_result"] == "WIN"


def test_weekly_open_option_stays_pending_after_entry_day(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.runtime_settings import success_rules

    monkeypatch.setattr(success_rules, "get", lambda category: {"threshold": 50.0, "unit": "USD"})
    trade = {
        "trade_id": "OPT-W7",
        "symbol": "MSFT",
        "trade_type": "EQUITY_OPTION_SWING",
        "status": "OPEN",
        "entry_confirmed": True,
        "filled_entry_price": 2.00,
        "entered_at": "2026-08-24T18:00:00+00:00",
        "max_profit_usd": 20.0,
        "contracts": 1,
        "option": {"type": "CALL", "strike": 500, "dte": 7, "expiration": "2026-08-31"},
    }
    m = _monitor()
    rows, changed = m._finalize_option_performance_rows(
        [dict(trade)], datetime(2026, 8, 25, 17, 0, tzinfo=ZoneInfo("America/New_York"))
    )
    assert changed is False
    assert rows[0].get("performance_result") is None


def test_monthly_open_option_stays_pending_before_expiry(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.runtime_settings import success_rules

    monkeypatch.setattr(success_rules, "get", lambda category: {"threshold": 50.0, "unit": "USD"})
    trade = {
        "trade_id": "OPT-M35",
        "symbol": "AAPL",
        "trade_type": "EQUITY_OPTION_SWING",
        "status": "OPEN",
        "entry_confirmed": True,
        "filled_entry_price": 3.00,
        "entered_at": "2026-08-20T18:00:00+00:00",
        "max_profit_usd": 30.0,
        "contracts": 1,
        "option": {"type": "CALL", "strike": 320, "dte": 30, "expiration": "2026-09-19"},
    }
    m = _monitor()
    rows, changed = m._finalize_option_performance_rows(
        [dict(trade)], datetime(2026, 8, 29, 17, 0, tzinfo=ZoneInfo("America/New_York"))
    )
    assert changed is False
    assert rows[0].get("performance_result") is None


def test_weekly_open_option_finalizes_loss_after_actual_expiry(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.runtime_settings import success_rules

    monkeypatch.setattr(success_rules, "get", lambda category: {"threshold": 50.0, "unit": "USD"})
    trade = {
        "trade_id": "OPT-WEXP",
        "symbol": "MSFT",
        "trade_type": "EQUITY_OPTION_SWING",
        "status": "OPEN",
        "entry_confirmed": True,
        "filled_entry_price": 2.00,
        "entered_at": "2026-08-24T18:00:00+00:00",
        "max_profit_usd": 20.0,
        "contracts": 1,
        "option": {"type": "CALL", "strike": 500, "dte": 5, "expiration": "2026-08-29"},
    }
    m = _monitor()
    rows, changed = m._finalize_option_performance_rows(
        [dict(trade)], datetime(2026, 8, 29, 16, 1, tzinfo=ZoneInfo("America/New_York"))
    )
    assert changed is True
    assert rows[0]["performance_result"] == "LOSS"
    assert rows[0]["performance_loss_reason"] == "THRESHOLD_NOT_REACHED_BY_EXPIRY"
