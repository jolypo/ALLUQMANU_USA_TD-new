from datetime import datetime, timezone

from app.reports.performance import category_period_report


def _opt(trade_id, entered_at, dte, category="equity_option", status="OPEN"):
    tt = "EQUITY_OPTION_SWING" if category == "equity_option" else "INDEX_OPTION_SWING"
    return {
        "trade_id": trade_id,
        "trade_type": tt,
        "status": status,
        "entry_confirmed": True,
        "filled_entry_price": 2.0,
        "entry_low": 1.95,
        "entry_high": 2.0,
        "entered_at": entered_at,
        "last_price": 2.2,
        "max_profit_usd": 20.0,
        "contracts": 1,
        "option": {"dte": dte, "strike": 100, "type": "CALL"},
    }


def test_daily_cohort_uses_entered_at_only_and_does_not_repeat_open_trade_tomorrow():
    trade = _opt("OPT-TODAY", "2026-08-28T16:00:00+00:00", 7)  # 12:00 ET Aug 28
    today = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    tomorrow = datetime(2026, 8, 29, 20, 0, tzinfo=timezone.utc)
    r1 = category_period_report([], [trade], "equity_option", "daily", today, horizon="all")
    r2 = category_period_report([], [trade], "equity_option", "daily", tomorrow, horizon="all")
    assert [r["trade_id"] for r in r1["rows"]] == ["OPT-TODAY"]
    assert r2["rows"] == []


def test_published_only_candidate_is_not_counted_in_daily_report():
    trade = _opt("OPT-NOFILL", None, 0)
    trade.pop("entered_at")
    trade["entry_confirmed"] = False
    trade["published_at"] = "2026-08-28T16:00:00+00:00"
    now = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    report = category_period_report([], [trade], "equity_option", "daily", now, horizon="all")
    assert report["rows"] == []


def test_daily_weekly_monthly_horizon_reports_are_separated():
    now = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    rows = [
        _opt("D0", "2026-08-28T16:00:00+00:00", 0),
        _opt("D7", "2026-08-28T16:05:00+00:00", 7),
        _opt("D35", "2026-08-28T16:10:00+00:00", 35),
    ]
    daily = category_period_report([], rows, "equity_option", "daily", now, horizon="daily")
    weekly = category_period_report([], rows, "equity_option", "daily", now, horizon="weekly")
    monthly = category_period_report([], rows, "equity_option", "daily", now, horizon="monthly")
    assert [r["trade_id"] for r in daily["rows"]] == ["D0"]
    assert [r["trade_id"] for r in weekly["rows"]] == ["D7"]
    assert [r["trade_id"] for r in monthly["rows"]] == ["D35"]


def test_comprehensive_options_daily_combines_equity_and_spx_all_horizons():
    now = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    rows = [
        _opt("EQ0", "2026-08-28T16:00:00+00:00", 0, "equity_option"),
        _opt("EQ7", "2026-08-28T16:05:00+00:00", 7, "equity_option"),
        _opt("SPX35", "2026-08-28T16:10:00+00:00", 35, "index_option"),
    ]
    report = category_period_report([], rows, "options_all", "daily", now, horizon="all")
    assert {r["trade_id"] for r in report["rows"]} == {"EQ0", "EQ7", "SPX35"}
    assert report["breakdown"]["daily"]["trades"] == 1
    assert report["breakdown"]["weekly"]["trades"] == 1
    assert report["breakdown"]["monthly"]["trades"] == 1
    assert report["breakdown"]["equity_option"]["trades"] == 2
    assert report["breakdown"]["index_option"]["trades"] == 1


def test_weekly_report_includes_only_entries_from_current_week():
    now = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)  # Friday ET
    current = _opt("THIS-WEEK", "2026-08-27T16:00:00+00:00", 7)
    prior = _opt("PRIOR-WEEK", "2026-08-20T16:00:00+00:00", 7)
    report = category_period_report([], [current, prior], "equity_option", "weekly", now, horizon="all")
    assert [r["trade_id"] for r in report["rows"]] == ["THIS-WEEK"]


def test_weekly_success_reached_today_is_win_in_same_day_report():
    now = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    trade = _opt("WEEKLY-WIN-TODAY", "2026-08-28T16:05:00+00:00", 7)
    trade["success_reached"] = True
    trade["performance_result"] = "WIN"
    trade["max_profit_usd"] = 60.0
    report = category_period_report([], [trade], "equity_option", "daily", now, horizon="all")
    assert report["summary"]["wins"] == 1
    assert report["summary"]["losses"] == 0
    assert report["summary"]["pending"] == 0
    assert report["breakdown"]["weekly"]["wins"] == 1


def test_weekly_open_without_success_is_pending_in_same_day_report():
    now = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    trade = _opt("WEEKLY-PENDING", "2026-08-28T16:05:00+00:00", 7)
    report = category_period_report([], [trade], "equity_option", "daily", now, horizon="all")
    assert report["summary"]["wins"] == 0
    assert report["summary"]["losses"] == 0
    assert report["summary"]["pending"] == 1
    assert report["breakdown"]["weekly"]["pending"] == 1


def test_closed_weekly_before_threshold_is_loss_immediately_in_entry_day_report():
    now = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    trade = _opt("WEEKLY-CLOSED", "2026-08-28T16:05:00+00:00", 7, status="CLOSED")
    trade["closed_at"] = "2026-08-28T18:00:00+00:00"
    trade["exit_price"] = 1.80
    trade["cash_pnl_usd"] = -20.0
    report = category_period_report([trade], [], "equity_option", "daily", now, horizon="all")
    assert report["summary"]["wins"] == 0
    assert report["summary"]["losses"] == 1
    assert report["summary"]["pending"] == 0
