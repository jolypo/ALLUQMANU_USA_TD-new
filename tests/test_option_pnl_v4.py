from app.scheduler.monitor import TradeMonitor


def test_option_put_is_long_premium_for_monitor_cash_pnl():
    monitor = TradeMonitor.__new__(TradeMonitor)
    trade = {
        "trade_type": "EQUITY_OPTION_INTRADAY",
        "direction": "SHORT",  # legacy underlying direction must not invert bought PUT premium P&L
        "filled_entry_price": 2.0,
        "contracts": 1,
        "option": {"type": "PUT"},
    }
    assert monitor._long(trade) is True
    usd, _ = monitor._cash(trade, 2.5)
    assert usd == 50.0
    assert round(monitor._pnl_pct(trade, 2.5), 2) == 25.0


def test_stop_at_entry_is_breakeven_not_loss():
    monitor = TradeMonitor.__new__(TradeMonitor)
    assert monitor._final_result_from_pnl(0.0) == "BREAKEVEN"
    assert monitor._final_result_from_pnl(0.5) == "WIN"
    assert monitor._final_result_from_pnl(-0.5) == "LOSS"
