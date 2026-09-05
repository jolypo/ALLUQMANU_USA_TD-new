from app.reports.performance import performance

def test_performance_basic():
    p=performance([{"status":"WIN","pnl_pct":10},{"status":"LOSS","pnl_pct":-5}])
    assert p["trades"]==2 and p["win_rate"]==50.0 and p["profit_factor"]==2.0


def test_open_success_is_counted_separately_from_final_results():
    closed_loss_after_success = {
        "trade_id": "OPT-1",
        "trade_type": "EQUITY_OPTION_INTRADAY",
        "status": "LOSS",
        "pnl_pct": -10,
        "success_reached": True,
    }
    still_open_success = {
        "trade_id": "IDX-1",
        "trade_type": "INDEX_OPTION_INTRADAY",
        "status": "OPEN",
        "success_reached": True,
        "last_price": 2.0,
        "filled_entry_price": 1.5,
    }
    p = performance([closed_loss_after_success], [still_open_success])
    assert p["successful_signals"] == 2
    assert p["successful_open"] == 1
    assert p["final_losses_after_success"] == 1
    # New contract-performance rule: reaching the threshold is the WIN used
    # by Performance even if the actual close later loses money.
    assert p["wins"] == 2
    assert p["losses"] == 0
    assert p["final_losses"] == 1


def test_stock_success_is_target_based_not_cash_threshold():
    stock_win = {
        "trade_id": "STK-WIN",
        "trade_type": "STOCK_INTRADAY",
        "status": "OPEN",
        "entry_confirmed": True,
        "filled_entry_price": 100.0,
        "tp1": 102.0,
        "tp1_hit": True,
        "last_price": 101.0,
    }
    stock_pending = {
        "trade_id": "STK-PENDING",
        "trade_type": "STOCK_INTRADAY",
        "status": "OPEN",
        "entry_confirmed": True,
        "filled_entry_price": 100.0,
        "tp1": 102.0,
        "last_price": 101.5,
    }
    p = performance([], [stock_win, stock_pending])
    assert p["wins"] == 1
    assert p["losses"] == 0
    assert p["pending"] == 1
