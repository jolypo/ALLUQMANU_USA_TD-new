import pytest

from app.runtime_settings import profit_alert_rules
from app.scheduler.monitor import TradeMonitor


def _monitor():
    return object.__new__(TradeMonitor)


def _trade(entry=7.0):
    return {
        "trade_id": "OPT-T",
        "symbol": "AAPL",
        "entry_confirmed": True,
        "filled_entry_price": entry,
        "entry_low": entry,
        "entry_high": entry,
        "option": {"type": "CALL", "strike": 200},
    }


def test_profit_alert_default_is_ten_cents():
    before = profit_alert_rules.get_step()
    try:
        profit_alert_rules.reset()
        assert profit_alert_rules.get_step() == pytest.approx(0.10)
    finally:
        profit_alert_rules.set_step(before)


def test_ten_cent_steps_are_anchored_to_entry():
    before = profit_alert_rules.get_step()
    try:
        profit_alert_rules.set_step(0.10)
        m = _monitor(); t = _trade(7.0)
        assert m._profit_alert_step_index(t, 7.09) == 0
        assert m._profit_alert_step_index(t, 7.10) == 1
        assert m._profit_alert_step_index(t, 7.19) == 1
        assert m._profit_alert_step_index(t, 7.20) == 2
        t["profit_alert_step_index"] = 2
        assert m._should_send_profit_alert(t, 7.15) is False
        assert m._should_send_profit_alert(t, 7.20) is False
        assert m._should_send_profit_alert(t, 7.30) is True
    finally:
        profit_alert_rules.set_step(before)


def test_five_cent_setting():
    before = profit_alert_rules.get_step()
    try:
        profit_alert_rules.set_step(0.05)
        m = _monitor(); t = _trade(7.0)
        assert m._should_send_profit_alert(t, 7.04) is False
        assert m._should_send_profit_alert(t, 7.05) is True
    finally:
        profit_alert_rules.set_step(before)
