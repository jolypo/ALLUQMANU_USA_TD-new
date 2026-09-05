from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import settings
from app.runtime_settings import success_rules

CLOSED = {"WIN", "LOSS", "BREAKEVEN", "CLOSED"}
NY = ZoneInfo("America/New_York")
OPTION_CATEGORIES = {"equity_option", "index_option"}


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _dt(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def _is_closed(t):
    return str(t.get("status", "")).upper() in CLOSED


def _is_entered(t: dict) -> bool:
    # Closed legacy rows necessarily represent entered trades even when older
    # saved JSON did not yet persist entry_confirmed/entered_at.
    return bool(
        t.get("entry_confirmed")
        or _safe_float(t.get("filled_entry_price"), 0) > 0
        or t.get("entered_at")
        or _is_closed(t)
    )


def _entry(t):
    filled = _safe_float(t.get("filled_entry_price"), 0)
    if filled > 0:
        return filled
    lo = _safe_float(t.get("entry_low"), 0)
    hi = _safe_float(t.get("entry_high"), 0)
    if lo > 0 and hi > 0:
        if t.get("option"):
            return max(lo, hi)
        short = str(t.get("direction", "LONG")).upper() == "SHORT"
        return min(lo, hi) if short else max(lo, hi)
    return max(lo, hi, 0)


def _pnl_pct(t, price=None):
    if price is None and t.get("pnl_pct") is not None:
        return _safe_float(t.get("pnl_pct"))
    price = _safe_float(price if price is not None else t.get("exit_price", t.get("last_price")), 0)
    e = _entry(t)
    if e <= 0 or price <= 0:
        return 0.0
    short = str(t.get("direction", "LONG")).upper() == "SHORT" and not t.get("option")
    return ((e - price) if short else (price - e)) / e * 100


def _cash_pnl(t, price=None):
    if "OPTION" not in str(t.get("trade_type", "")).upper():
        return 0.0
    if price is None and t.get("cash_pnl_usd") is not None:
        return _safe_float(t.get("cash_pnl_usd"), 0.0)
    price = _safe_float(price if price is not None else t.get("exit_price", t.get("last_price")), 0)
    e = _entry(t)
    if e <= 0 or price <= 0:
        return 0.0
    contracts = max(1, int(_safe_float(t.get("contracts", 1), 1)))
    return (price - e) * settings.option_multiplier * contracts


def _category(tt: str):
    tt = str(tt).upper()
    if tt.startswith("STOCK_"):
        return "stock"
    if tt.startswith("EQUITY_OPTION_"):
        return "equity_option"
    if tt.startswith("INDEX_OPTION_"):
        return "index_option"
    return "other"


def _success_reached(t: dict) -> bool:
    category = _category(str(t.get("trade_type", "")))
    if category == "stock":
        return _stock_target_reached(t)

    if category not in OPTION_CATEGORIES:
        return False
    if str(t.get("performance_result", "")).upper() == "LOSS":
        return False
    if bool(t.get("success_reached")) or bool(t.get("success_100_reached")):
        return True
    rule = success_rules.get(category)
    threshold = _safe_float(rule.get("threshold"), 0.0)
    if threshold <= 0:
        return False
    return _safe_float(t.get("max_profit_usd"), 0.0) >= threshold


def _normalized_result(t):
    """Actual realized result kept for audit, independent from score result."""
    if t.get("pnl_pct") is not None:
        p = _safe_float(t.get("pnl_pct"), 0.0)
        if p > 0.01:
            return "WIN"
        if p < -0.01:
            return "LOSS"
        return "BREAKEVEN"
    if "OPTION" in str(t.get("trade_type", "")).upper() and t.get("cash_pnl_usd") is not None:
        cash = _safe_float(t.get("cash_pnl_usd"), 0.0)
        if cash > 0.01:
            return "WIN"
        if cash < -0.01:
            return "LOSS"
        return "BREAKEVEN"
    explicit = str(t.get("final_result", "")).upper()
    if explicit in {"WIN", "LOSS", "BREAKEVEN"}:
        return explicit
    status = str(t.get("status", "")).upper()
    if status in {"WIN", "LOSS", "BREAKEVEN"}:
        return status
    return "BREAKEVEN"


def _stock_target_reached(t: dict) -> bool:
    if any(bool(t.get(f"tp{n}_hit")) for n in (1, 2, 3)):
        return True
    # Backward-compatible inference for older saved trades where TP flags may
    # not exist but a best observed price/P&L is available.
    tp1 = _safe_float(t.get("tp1"), 0.0)
    if tp1 <= 0 or not _is_entered(t):
        return False
    best = _best_price(t)
    if best <= 0:
        return False
    short = str(t.get("direction", "LONG")).upper() == "SHORT"
    return best <= tp1 if short else best >= tp1


def _score_result(t: dict) -> str:
    """User-facing performance classification.

    Options:
      WIN immediately when the configured cash threshold is reached.
      LOSS only after the US session has ended and the monitor has finalized
      the unresolved trade. Closing P&L never changes this threshold result.

    Stocks:
      WIN when a target is reached. A closed entered stock that never reached
      a target is LOSS. Open stocks remain OPEN until resolved.
    """
    if not _is_entered(t):
        return "OPEN"
    category = _category(str(t.get("trade_type", "")))
    if category in OPTION_CATEGORIES:
        explicit = str(t.get("performance_result", "")).upper()
        if explicit in {"WIN", "LOSS"}:
            return explicit
        if _success_reached(t):
            return "WIN"
        # Once an option trade has actually closed, it can no longer reach the
        # configured cash-success threshold. Under the project's statistical
        # success rule, a closed option that never reached the threshold is
        # therefore finalized as LOSS immediately. Open Weekly/Monthly trades
        # remain OPEN/PENDING until success, real close, or expiry.
        if _is_closed(t):
            return "LOSS"
        return "OPEN"
    if category == "stock":
        if _stock_target_reached(t):
            return "WIN"
        return "LOSS" if _is_closed(t) else "OPEN"
    if _is_closed(t):
        result = _normalized_result(t)
        return result if result in {"WIN", "LOSS"} else "OPEN"
    return "OPEN"


def _max_drawdown(rows):
    equity = peak = mdd = 0.0
    for t in rows:
        equity += _pnl_pct(t)
        peak = max(peak, equity)
        mdd = min(mdd, equity - peak)
    return round(mdd, 2)


def _dedupe(rows: list[dict]) -> list[dict]:
    seen = {}
    for t in rows:
        key = str(t.get("trade_id") or id(t))
        seen[key] = t
    return list(seen.values())


def performance(history: list[dict], open_trades: list[dict] | None = None) -> dict:
    """Performance using the user's scoring rule plus realized audit metrics."""
    all_rows = [t for t in _dedupe(list(history) + list(open_trades or [])) if _is_entered(t)]
    scored_wins = [t for t in all_rows if _score_result(t) == "WIN"]
    scored_losses = [t for t in all_rows if _score_result(t) == "LOSS"]
    pending = [t for t in all_rows if _score_result(t) == "OPEN"]
    scored = scored_wins + scored_losses

    closed = [t for t in history if _is_closed(t) and _is_entered(t)]
    final_wins = [t for t in closed if _normalized_result(t) == "WIN"]
    final_losses = [t for t in closed if _normalized_result(t) == "LOSS"]
    final_be = [t for t in closed if _normalized_result(t) == "BREAKEVEN"]
    pnls = [_pnl_pct(t) for t in closed]
    gw = sum(x for x in pnls if x > 0)
    gl = abs(sum(x for x in pnls if x < 0))
    pf = gw / gl if gl else (999.0 if gw else 0.0)

    return {
        # Primary performance = requested score rule.
        "trades": len(scored),
        "wins": len(scored_wins),
        "losses": len(scored_losses),
        "breakeven": 0,
        "win_rate": round(len(scored_wins) / len(scored) * 100, 2) if scored else 0.0,
        "activity": len(all_rows),
        "pending": len(pending),
        "successful_signals": len(scored_wins),
        "successful_open": sum(1 for t in scored_wins if not _is_closed(t)),
        "final_losses_after_success": sum(
            1 for t in scored_wins if _is_closed(t) and _normalized_result(t) == "LOSS"
        ),
        "success_rate": round(len(scored_wins) / len(scored) * 100, 2) if scored else 0.0,
        # Realized close metrics retained for audit/debugging and risk review.
        "final_closed_trades": len(closed),
        "final_wins": len(final_wins),
        "final_losses": len(final_losses),
        "final_breakeven": len(final_be),
        "final_win_rate": round(len(final_wins) / len(closed) * 100, 2) if closed else 0.0,
        "net_pnl_pct": round(sum(pnls), 2),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": _max_drawdown(closed),
    }


def _period_bounds(period: str, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(NY)
    if period == "daily":
        start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
    elif period == "weekly":
        start_local = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=7)
    else:
        raise ValueError("period must be daily or weekly")
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), local


def _in_period(t: dict, start: datetime, end: datetime) -> bool:
    """Assign a trade to a report period strictly by its confirmed entry time.

    Published-only signals are not trades yet, and an older swing option must
    not be counted again tomorrow merely because it remains open or closes
    later. `entered_at` is therefore the canonical cohort timestamp.
    """
    stamp = _dt(t.get("entered_at"))
    return bool(stamp and start <= stamp < end)


def _option_horizon(t: dict) -> str | None:
    option = t.get("option") or {}
    try:
        dte = int(float(option.get("dte")))
    except (TypeError, ValueError):
        # Newer trades persist the selected horizon; use it only when an
        # explicit DTE is unavailable.
        raw = str(option.get("horizon") or t.get("horizon") or "").lower()
        if raw in {"daily", "weekly", "monthly"}:
            return raw
        return None
    if dte == 0:
        return "daily"
    if 1 <= dte <= 7:
        return "weekly"
    if 8 <= dte <= 35:
        return "monthly"
    return None


def _matches_horizon(t: dict, horizon: str | None) -> bool:
    if horizon is None or horizon == "all":
        return True
    return _option_horizon(t) == horizon


def _best_price(t: dict) -> float:
    entry = _entry(t)
    if entry <= 0:
        return 0.0
    category = _category(str(t.get("trade_type", "")))
    if category in OPTION_CATEGORIES:
        max_usd = _safe_float(t.get("max_profit_usd"), 0.0)
        contracts = max(1, int(_safe_float(t.get("contracts", 1), 1)))
        if max_usd > 0:
            return entry + max_usd / (settings.option_multiplier * contracts)
    max_pct = _safe_float(t.get("max_pnl_pct"), 0.0)
    if max_pct > 0:
        short = str(t.get("direction", "LONG")).upper() == "SHORT" and not t.get("option")
        return entry * (1 - max_pct / 100) if short else entry * (1 + max_pct / 100)
    return max(entry, _safe_float(t.get("last_price"), 0.0), _safe_float(t.get("exit_price"), 0.0))


def _display_row(t: dict) -> dict:
    category = _category(str(t.get("trade_type", "")))
    option = t.get("option") or {}
    result = _score_result(t)
    if category in OPTION_CATEGORIES:
        contracts = max(1, int(_safe_float(t.get("contracts", 1), 1)))
        best_profit = max(0.0, _safe_float(t.get("max_profit_usd"), 0.0))
        final_value = _cash_pnl(t) if _is_closed(t) else _cash_pnl(t, t.get("last_price"))
        contract_label = str(option.get("strike", "N/A"))
        if category == "equity_option":
            contract_label = f"{t.get('symbol', '')} {contract_label}".strip()
        return {
            "contract": contract_label,
            "kind": str(option.get("type", "N/A")).upper(),
            "entry": round(_entry(t), 4),
            "best_price": round(_best_price(t), 4),
            "best_profit": round(best_profit, 2),
            "final_value": round(final_value, 2),
            "result": result,
            "success": result == "WIN",
            "contracts": contracts,
            "trade_id": t.get("trade_id", ""),
        }

    best_pct = max(_safe_float(t.get("max_pnl_pct"), 0.0), 0.0)
    return {
        "contract": str(t.get("symbol", "N/A")),
        "kind": str(t.get("direction", "LONG")).upper(),
        "entry": round(_entry(t), 4),
        "best_price": round(_best_price(t), 4),
        "best_profit": round(best_pct, 2),
        "final_value": round(_pnl_pct(t), 2),
        "result": result,
        "success": result == "WIN",
        "contracts": None,
        "trade_id": t.get("trade_id", ""),
    }


def _option_score_financial(rows: list[dict]) -> dict:
    """Financial score matching the report model requested by the user.

    Successful option trades contribute their best observed cash profit.
    A trade finalized as LOSS at session end contributes one full paid premium
    as the statistical loss. Actual realized P&L remains stored on the trade and
    is returned separately by category_period_report for audit purposes.
    """
    wins = [t for t in rows if _score_result(t) == "WIN"]
    losses = [t for t in rows if _score_result(t) == "LOSS"]
    gross_profit = sum(max(0.0, _safe_float(t.get("max_profit_usd"), 0.0)) for t in wins)
    gross_loss = 0.0
    for t in losses:
        contracts = max(1, int(_safe_float(t.get("contracts", 1), 1)))
        gross_loss += _entry(t) * settings.option_multiplier * contracts
    return {
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net": gross_profit - gross_loss,
    }


def category_period_report(
    history: list[dict],
    open_trades: list[dict],
    category: str,
    period: str,
    now=None,
    horizon: str | None = None,
) -> dict:
    if category not in {"stock", "equity_option", "index_option", "options_all"}:
        raise ValueError("invalid category")
    if horizon not in {None, "all", "daily", "weekly", "monthly"}:
        raise ValueError("invalid horizon")
    if category == "stock" and horizon not in {None, "all"}:
        raise ValueError("stock reports do not use option horizons")

    start, end, local_now = _period_bounds(period, now)

    def category_ok(t: dict) -> bool:
        actual = _category(str(t.get("trade_type", "")))
        if category == "options_all":
            return actual in OPTION_CATEGORIES
        return actual == category

    def include(t: dict) -> bool:
        return (
            category_ok(t)
            and _is_entered(t)
            and _in_period(t, start, end)
            and _matches_horizon(t, horizon)
        )

    history_rows = [t for t in history if include(t)]
    open_rows = [t for t in open_trades if include(t)]
    all_rows = _dedupe(history_rows + open_rows)
    closed = [t for t in history_rows if _is_closed(t)]
    active_open = [t for t in open_rows if not _is_closed(t)]
    summary = performance(history_rows, active_open)

    is_option_report = category in OPTION_CATEGORIES or category == "options_all"
    if is_option_report:
        scored = _option_score_financial(all_rows)
        gross_profit, gross_loss, net = scored["gross_profit"], scored["gross_loss"], scored["net"]
        realized = [_cash_pnl(t) for t in closed]
        realized_gp = sum(v for v in realized if v > 0)
        realized_gl = abs(sum(v for v in realized if v < 0))
        realized_net = sum(realized)
        unit = "USD"
    else:
        realized = [_pnl_pct(t) for t in closed]
        gross_profit = sum(v for v in realized if v > 0)
        gross_loss = abs(sum(v for v in realized if v < 0))
        net = sum(realized)
        realized_gp, realized_gl, realized_net = gross_profit, gross_loss, net
        unit = "PCT"

    rule = success_rules.get(category) if category != "options_all" else {}
    display_rows = [_display_row(t) for t in all_rows]
    order = {"WIN": 2, "LOSS": 1, "OPEN": 0}
    display_rows.sort(key=lambda r: (order.get(r["result"], 0), r["best_profit"]), reverse=True)

    breakdown = {}
    if is_option_report:
        for key in ("daily", "weekly", "monthly"):
            subset = [t for t in all_rows if _option_horizon(t) == key]
            scored_subset = _option_score_financial(subset)
            breakdown[key] = {
                "trades": len(subset),
                "wins": sum(1 for t in subset if _score_result(t) == "WIN"),
                "losses": sum(1 for t in subset if _score_result(t) == "LOSS"),
                "pending": sum(1 for t in subset if _score_result(t) == "OPEN"),
                "net": round(scored_subset["net"], 2),
            }
        if category == "options_all":
            breakdown["equity_option"] = {
                "trades": sum(1 for t in all_rows if _category(str(t.get("trade_type", ""))) == "equity_option")
            }
            breakdown["index_option"] = {
                "trades": sum(1 for t in all_rows if _category(str(t.get("trade_type", ""))) == "index_option")
            }

    return {
        "category": category,
        "period": period,
        "horizon": horizon or "all",
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "report_date_ny": local_now.date().isoformat(),
        "summary": summary,
        "closed_rows": closed,
        "open_rows": active_open,
        "rows": all_rows,
        "display_rows": display_rows,
        "success_rule": rule,
        "breakdown": breakdown,
        "financial": {
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "net": round(net, 2),
            "gross_profit_sar": round(gross_profit * settings.usd_sar_rate, 2) if unit == "USD" else 0.0,
            "gross_loss_sar": round(gross_loss * settings.usd_sar_rate, 2) if unit == "USD" else 0.0,
            "net_sar": round(net * settings.usd_sar_rate, 2) if unit == "USD" else 0.0,
            "unit": unit,
            "basis": "SUCCESS_THRESHOLD" if is_option_report else "REALIZED_STOCK_PCT",
        },
        "realized_financial": {
            "gross_profit": round(realized_gp, 2),
            "gross_loss": round(realized_gl, 2),
            "net": round(realized_net, 2),
        },
    }


def comprehensive_options_report(history: list[dict], open_trades: list[dict], period: str, now=None) -> dict:
    return category_period_report(history, open_trades, "options_all", period, now, horizon="all")

def daily_category_reports(history: list[dict], open_trades: list[dict], now=None) -> dict:
    return {category: category_period_report(history, open_trades, category, "daily", now) for category in ("stock", "equity_option", "index_option")}


def weekly_category_report(history: list[dict], open_trades: list[dict], category: str, now=None) -> dict:
    return category_period_report(history, open_trades, category, "weekly", now)


def daily_report_data(history: list[dict], now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    start, end, _ = _period_bounds("daily", now)
    rows = [t for t in history if _is_entered(t) and _in_period(t, start, end)]
    return {"summary": performance(rows), "rows": rows}


def weekly_report_data(history: list[dict], open_trades: list[dict], now=None) -> dict:
    start, end, _ = _period_bounds("weekly", now)
    history_rows = [t for t in history if _is_entered(t) and _in_period(t, start, end)]
    open_rows = [t for t in open_trades if _is_entered(t) and _in_period(t, start, end)]
    return {
        "summary": performance(history_rows, open_rows),
        "closed_rows": [t for t in history_rows if _is_closed(t)],
        "open_rows": [t for t in open_rows if not _is_closed(t)],
        "open_summary": {
            "total": len(open_rows),
            "successful": sum(1 for t in open_rows if _score_result(t) == "WIN"),
            "pending": sum(1 for t in open_rows if _score_result(t) == "OPEN"),
            "unrealized_pnl_pct": round(sum(_pnl_pct(t) for t in open_rows), 2),
        },
    }
