from datetime import datetime, timezone
from pathlib import Path

from app.reports.performance import performance, category_period_report
from app.reports.weekly_card import performance_report_card


def _option_trade(**overrides):
    row = {
        "trade_id": "OPT-1",
        "trade_type": "EQUITY_OPTION_INTRADAY",
        "status": "CLOSED",
        "direction": "LONG",
        "filled_entry_price": 3.90,
        "entry_low": 3.80,
        "entry_high": 3.90,
        "exit_price": 4.40,
        "last_price": 4.40,
        "pnl_pct": 12.82,
        "cash_pnl_usd": 50.0,
        "cash_pnl_sar": 187.5,
        "max_profit_usd": 80.0,
        "max_pnl_pct": 20.5,
        "success_reached": True,
        "success_threshold_value": 50.0,
        "success_threshold_unit": "USD",
        "published_at": "2026-08-28T00:15:00+00:00",  # Aug 27 NY
        "entered_at": "2026-08-28T00:20:00+00:00",
        "closed_at": "2026-08-28T00:45:00+00:00",
        "option": {"strike": 185, "type": "CALL"},
    }
    row.update(overrides)
    return row


def test_realized_pnl_is_source_of_truth_for_final_win_loss():
    # Even if a legacy status is inconsistent, realized P&L wins.
    p = performance([_option_trade(status="LOSS", pnl_pct=10.0, cash_pnl_usd=39.0)])
    assert p["wins"] == 1
    assert p["losses"] == 0


def test_success_milestone_is_performance_win_even_if_realized_close_loses():
    p = performance([_option_trade(status="LOSS", pnl_pct=-12.0, cash_pnl_usd=-46.8)])
    assert p["successful_signals"] == 1
    assert p["final_losses_after_success"] == 1
    assert p["wins"] == 1
    assert p["losses"] == 0
    assert p["final_losses"] == 1


def test_daily_report_uses_new_york_trading_date():
    now = datetime(2026, 8, 28, 1, 0, tzinfo=timezone.utc)  # Aug 27 21:00 ET
    report = category_period_report([_option_trade()], [], "equity_option", "daily", now)
    assert report["report_date_ny"] == "2026-08-27"
    assert report["summary"]["wins"] == 1
    # Report model uses the best observed profit for a successful option.
    assert report["financial"]["net"] == 80.0
    assert report["financial"]["net_sar"] == 300.0


def test_unified_report_card_renders(tmp_path: Path):
    now = datetime(2026, 8, 28, 1, 0, tzinfo=timezone.utc)
    report = category_period_report([_option_trade()], [], "equity_option", "daily", now)
    path = tmp_path / "daily.png"
    performance_report_card(report, str(path))
    assert path.exists()
    assert path.stat().st_size > 10_000
