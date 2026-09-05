import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from app.config import settings
from app.options.selector import ContractSelector
from app.runtime_settings import contract_search_rules
from app.scheduler.monitor import TradeMonitor


def _option_snapshot(ask=4.8, bid=4.6, delta=0.55):
    return {
        "latestQuote": {"bp": bid, "ap": ask, "t": datetime.now(timezone.utc).isoformat()},
        "greeks": {"delta": delta, "theta": -0.05, "gamma": 0.01, "vega": 0.1, "rho": 0.01},
        "dailyBar": {"v": 500},
        "impliedVolatility": 0.3,
    }


def test_candidate_ttl_is_three_minutes():
    assert settings.candidate_ttl_seconds == 180


def test_option_selector_respects_max_contract_price():
    today = datetime.now().date()
    # Use future-ish OCC date safely based on today + 7.
    d = (today + timedelta(days=7)).strftime("%y%m%d")
    sym = f"AAPL{d}C00200000"
    payload = {"snapshots": {sym: _option_snapshot(ask=5.20, bid=5.00)}}
    selector = ContractSelector()
    assert selector.select(
        payload, "LONG", "AAPL", 200.0, min_dte=0, max_dte=30,
        max_contract_price=5.0,
    ) is None
    row = selector.select(
        payload, "LONG", "AAPL", 200.0, min_dte=0, max_dte=30,
        max_contract_price=10.0,
    )
    assert row is not None
    assert row["ask"] == 5.2


def test_contract_search_store_set_and_reset(tmp_path, monkeypatch):
    # Exercise the public singleton but restore all six horizon values.
    before = contract_search_rules.all()
    try:
        contract_search_rules.set_max_price("equity_option", "daily", 3)
        contract_search_rules.set_max_price("equity_option", "weekly", 5)
        contract_search_rules.set_max_price("equity_option", "monthly", 10)
        contract_search_rules.set_max_price("index_option", "daily", 8)
        contract_search_rules.set_max_price("index_option", "weekly", 15)
        contract_search_rules.set_max_price("index_option", "monthly", 25)
        assert contract_search_rules.get_max_price("equity_option", "daily") == 3
        assert contract_search_rules.get_max_price("equity_option", "weekly") == 5
        assert contract_search_rules.get_max_price("equity_option", "monthly") == 10
        assert contract_search_rules.get_max_price("index_option", "daily") == 8
        assert contract_search_rules.get_max_price("index_option", "weekly") == 15
        assert contract_search_rules.get_max_price("index_option", "monthly") == 25
        contract_search_rules.reset()
        for h in contract_search_rules.HORIZONS:
            assert contract_search_rules.get_max_price("equity_option", h) == settings.equity_option_max_contract_price_default
            assert contract_search_rules.get_max_price("index_option", h) == settings.index_option_max_contract_price_default
    finally:
        for category in contract_search_rules.CATEGORIES:
            for horizon in contract_search_rules.HORIZONS:
                contract_search_rules.set_max_price(category, horizon, before[category][horizon])


def test_monitor_control_callbacks_exist():
    source = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "telegram" / "bots.py").read_text()
    assert 'callback_data=f"monitor:start:{key}"' in source
    assert 'callback_data=f"monitor:stop:{key}"' in source
    assert 'callback_data=f"monitor:scan:{key}"' in source
    assert 'watch:approve:' in source
    assert 'settings.monitor_max_opportunities' in source


def test_candidate_message_contains_required_contract_fields():
    source = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "telegram" / "message_templates.py").read_text()
    for expected in [
        "تاريخ الانتهاء", "المتبقي للانتهاء", "سعر العقد الحالي", "Bid:", "Ask:",
        "Mid:", "سعر السهم الحالي", "اكتشاف العقد — نيويورك", "انتهاء صلاحية الدخول", "قوة الإشارة",
    ]:
        assert expected in source


@pytest.mark.asyncio
async def test_profit_update_suppressed_below_entry(monkeypatch, tmp_path):
    monitor = object.__new__(TradeMonitor)
    sent = []
    monitor.profit_bot = object()
    monitor.channel_id = 1

    async def fake_send_photo(*args, **kwargs):
        sent.append((args, kwargs))

    monkeypatch.setattr(monitor, "_send_photo", fake_send_photo)
    trade = {
        "trade_id": "OPT-X", "symbol": "MU", "trade_type": "EQUITY_OPTION_INTRADAY",
        "entry_confirmed": True, "filled_entry_price": 33.60,
        "option": {"type": "CALL", "strike": 935},
    }
    result = await monitor._profit_update(trade, 33.11, 33.37)
    assert result is False
    assert sent == []



@pytest.mark.asyncio
async def test_first_profit_cross_sets_profit_alert_flags(monkeypatch, tmp_path):
    from app.scheduler.monitor import TradeMonitor
    class _Bot:
        async def send_photo(self, *args, **kwargs):
            return True
        async def send_message(self, *args, **kwargs):
            return True
    class _Repo:
        def all(self):
            return []
        def replace(self, rows):
            return None
        def append(self, row):
            return None
    mon = TradeMonitor(_Repo(), _Repo(), _Repo(), object(), _Bot(), _Bot(), _Bot(), channel_id='x')
    trade = {
        "trade_id": "OPT-TEST",
        "symbol": "AAPL",
        "contracts": 1,
        "entry_confirmed": True,
        "filled_entry_price": 2.31,
        "entry_low": 2.29,
        "entry_high": 2.31,
        "option": {"type": "CALL", "strike": 320},
    }
    result = await mon._profit_update(trade, 2.31, 2.45)
    assert result is True
    assert trade["profit_alert_sent"] is True
    assert trade["profit_alert_last_usd"] > 0
