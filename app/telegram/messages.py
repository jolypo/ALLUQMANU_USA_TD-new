from datetime import datetime, timezone
from zoneinfo import ZoneInfo

def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _time_diagnostics(s: dict, option: dict) -> list[str]:
    detected_raw = s.get("_candidate_detected_at") or s.get("detected_at") or s.get("published_at") or s.get("created_at")
    data_raw = option.get("quote_timestamp") or option.get("underlying_data_timestamp") or s.get("market_timestamp")
    detected = _parse_dt(detected_raw)
    data_dt = _parse_dt(data_raw)
    ny = ZoneInfo("America/New_York")
    riyadh = ZoneInfo("Asia/Riyadh")
    rows = []
    if detected:
        rows.append(f"🕒 اكتشاف النظام ET: {detected.astimezone(ny).strftime('%Y-%m-%d %I:%M:%S %p')}")
        rows.append(f"🕒 اكتشاف النظام KSA: {detected.astimezone(riyadh).strftime('%Y-%m-%d %I:%M:%S %p')}")
    else:
        rows.append("🕒 اكتشاف النظام: UNAVAILABLE")
    rows.append(f"📡 وقت بيانات السوق: {data_raw or 'UNAVAILABLE'}")
    if detected and data_dt:
        lag = max(0, int((detected.astimezone(timezone.utc) - data_dt.astimezone(timezone.utc)).total_seconds()))
        rows.append(f"⏱ تأخر البيانات/الاكتشاف: {lag // 60}m {lag % 60}s")
    else:
        rows.append("⏱ تأخر البيانات/الاكتشاف: UNAVAILABLE")
    if s.get("published_at"):
        pub = _parse_dt(s.get("published_at"))
        if pub:
            rows.append(f"📨 وقت النشر ET: {pub.astimezone(ny).strftime('%Y-%m-%d %I:%M:%S %p')}")
    return rows


def _data_context_lines(option: dict) -> list[str]:
    mode = str(option.get("strategy_mode", "")).upper()
    rows = list(option.get("market_context_lines") or [])
    if mode in {"WASEEM_V2", "WASEEM_V3", "WASEEM_V4"}:
        return rows or ["Context data: UNAVAILABLE"]
    return ["Advanced V2/V3 context: NOT USED BY THIS ENGINE"]


def _gth_diag_lines(option: dict) -> list[str]:
    d = option.get("gth_data_diagnostics") or {}
    if not d:
        return ["• SPXW GTH Data Diagnostics: NOT APPLICABLE / UNAVAILABLE"]
    return [
        f'• GTH Session: {d.get("session", "UNAVAILABLE")} | Open: {d.get("session_open", False)} | Trade Date: {d.get("trade_date", "UNAVAILABLE")}',
        f'• Session ET: {d.get("session_start_et", "UNAVAILABLE")} → {d.get("session_end_et", "UNAVAILABLE")}',
        f'• Session KSA: {d.get("session_start_ksa", "UNAVAILABLE")} → {d.get("session_end_ksa", "UNAVAILABLE")}',
        f'• Options Feed: {d.get("options_feed", "UNAVAILABLE")} | Overall Status: {d.get("option_data_status", "UNAVAILABLE")}',
        f'• Quote Status: {d.get("latest_quote_status", "UNAVAILABLE")} | Trade Status: {d.get("latest_trade_status", "UNAVAILABLE")}',
        f'• Chain Source: {d.get("chain_source", "UNAVAILABLE")}',
        f'• Snapshots/Quotes/Trades: {d.get("snapshot_count", 0)} / {d.get("quote_count", 0)} / {d.get("trade_count", 0)}',
        f'• Latest Quote Contract: {d.get("latest_quote_contract", "UNAVAILABLE")}',
        f'• Latest Quote Time: {d.get("latest_quote_time", "UNAVAILABLE")} | Age: {d.get("latest_quote_age_minutes", "UNAVAILABLE")}m',
        f'• Latest Quote Bid/Ask: {d.get("latest_quote_bid", "UNAVAILABLE")} / {d.get("latest_quote_ask", "UNAVAILABLE")}',
        f'• Latest Trade Contract: {d.get("latest_trade_contract", "UNAVAILABLE")}',
        f'• Latest Trade Time: {d.get("latest_trade_time", "UNAVAILABLE")} | Age: {d.get("latest_trade_age_minutes", "UNAVAILABLE")}m',
        f'• Latest Trade Price: {d.get("latest_trade_price", "UNAVAILABLE")}',
        f'• Cash SPX Last Point: {d.get("cash_last_point_price", "UNAVAILABLE")} | {d.get("cash_last_point_time", "UNAVAILABLE")}',
        f'• Cash SPX Point Session: {d.get("cash_last_point_session", "UNAVAILABLE")} | Age: {d.get("cash_last_point_age_minutes", "UNAVAILABLE")}m',
        f'• Diagnostics Checked At: {d.get("checked_at", "UNAVAILABLE")}',
        f'• Feed Errors: {", ".join(d.get("errors") or []) if d.get("errors") else "NONE"}',
    ]

def trade_type_ar(v: str) -> str:
    return {
        "STOCK_INTRADAY": "سهم أمريكي — مضاربة يومية",
        "STOCK_SWING": "سهم أمريكي — سوينغ",
        "EQUITY_OPTION_INTRADAY": "خيارات سهم — مضاربة يومية",
        "EQUITY_OPTION_SWING": "خيارات سهم — سوينغ",
        "INDEX_OPTION_INTRADAY": "خيارات مؤشر — مضاربة يومية",
        "INDEX_OPTION_SWING": "خيارات مؤشر — سوينغ",
    }.get(v, v)


def signal_text(s: dict) -> str:
    prob = (
        f'{s.get("probability"):.1f}%'
        if s.get("probability_status") == "VALIDATED" and s.get("probability") is not None
        else "غير موثقة إحصائيًا بعد"
    )
    option = s.get("option") or {}
    under_dir = option.get("underlying_direction", s.get("direction"))
    dir_ar = "صاعد" if under_dir == "LONG" else "هابط"
    is_option = bool(option)
    title = "🚨 فرصة Options جديدة" if is_option else "🚨 فرصة تداول جديدة"
    strategy_mode = str(option.get("strategy_mode", "")).upper()
    strategy_label = "وسيم V4" if strategy_mode == "WASEEM_V4" else "وسيم V3" if strategy_mode == "WASEEM_V3" else "وسيم V2" if strategy_mode == "WASEEM_V2" else "وسيم V1" if strategy_mode == "WASEEM_V1" else "SPX V20" if strategy_mode == "SPX_V20" else "SPX Core" if strategy_mode == "SPX_CORE" else "Confirmed Setup" if strategy_mode == "CONFIRMED_SETUP" else None
    lines = [
        title,
        f'الأصل: {s["symbol"]}',
        f'📈 الاتجاه: {dir_ar}',
        f'🧭 نوع الصفقة: {trade_type_ar(s["trade_type"])}',
    ]
    if strategy_label:
        lines.append(f'🧠 المحرك: {strategy_label}')
    lines += _time_diagnostics(s, option)
    lines += [
        f'💰 منطقة الدخول: {s["entry_low"]} – {s["entry_high"]}',
        f'🛑 وقف الخسارة/حارس العقد: {s["stop"]}',
        f'🎯 TP1: {s["tp1"]}',
        f'🎯 TP2: {s["tp2"]}',
        f'🎯 TP3: {s["tp3"]}',
        f'⭐ قوة الإشارة: {s["score"]}/100',
        f'⚖️ R/R النظري: 1 : {s["rr"]}',
        f'🛡️ المخاطرة المقترحة: {s["risk_pct"]*100:.2f}%',
        f'📊 الاحتمالية الإحصائية: {prob}',
        f'🧪 العينات المتشابهة: {s.get("probability_samples", 0)}',
        f'📌 الحالة الإحصائية: {s.get("probability_status", "UNVALIDATED")}',
        f'📈 حالة السوق: {s.get("market_regime", "UNKNOWN")}',
        f'🌐 بوابة السوق: {s.get("market_state", "NORMAL")} | الحد المطلوب: {s.get("required_score", "N/A")}',
        f'💧 السيولة: {s.get("liquidity_state", "NORMAL")} | 🌪️ التذبذب: {s.get("volatility_state", "NORMAL")}',
        f'🏦 القطاع: {s.get("sector", "N/A")}',
        "📌 أسباب الصفقة:",
    ]
    lines += [f'• {x}' for x in s.get("reasons", [])]
    if s.get("strategies"):
        lines += ["🧠 المحاور المتوافقة:", "• " + " • ".join(s.get("strategies", []))]
    if s.get("invalidation"):
        lines += ["⚠️ متى يبطل السيناريو؟"] + [f'• {x}' for x in s["invalidation"]]
    if option:
        lines += [
            "📄 بيانات العقد:",
            f'• النوع: {option.get("type", "N/A")}',
            f'• Strike: {option.get("strike", "N/A")}',
            f'• Expiration: {option.get("expiration", "N/A")}',
            f'• DTE: {option.get("dte", "N/A")}',
            f'• Bid: ${option.get("bid", "N/A")} | Ask: ${option.get("ask", "N/A")}',
            f'• Spread: {option.get("spread_pct", "N/A")}% | Contract Score: {option.get("contract_score", "N/A")}',
            f'• Engine: {option.get("engine_source", option.get("strategy_mode", "N/A"))}',
            f'• Expected Move: {option.get("expected_move", "N/A")} | Strike Distance: {option.get("strike_distance", "N/A")}',
            "📊 Greeks:",
            f'• Delta: {option.get("delta", "N/A")} | Gamma: {option.get("gamma", "N/A")}',
            f'• Theta: {option.get("theta", "N/A")} | Vega: {option.get("vega", "N/A")}',
            f'• IV: {option.get("iv") if option.get("iv") is not None else "N/A"}',
            "📈 بيانات الأصل الأساسي:",
            f'• الدخول: {option.get("underlying_entry_low", "N/A")} – {option.get("underlying_entry_high", "N/A")}',
            f'• مستوى الإبطال: {option.get("underlying_stop", "N/A")}',
            f'• TP1/TP2/TP3: {option.get("underlying_tp1", "N/A")} / {option.get("underlying_tp2", "N/A")} / {option.get("underlying_tp3", "N/A")}',
            "📡 جودة بيانات الخيارات: INDICATIVE — ليست OPRA Real-Time",
        ]
        if strategy_mode in {"WASEEM_V3", "WASEEM_V4"}:
            lines += [
                "🧪 Waseem V3 — Entry Engine:",
                f'• Entry State: {option.get("entry_state", "N/A")}',
                f'• First Detected At: {s.get("first_detected_at", "N/A")}',
                f'• Watch Added At: {s.get("watch_added_at", "N/A")}',
                f'• Entry Ready At: {s.get("entry_ready_at", "N/A")}',
                f'• Current Premium: ${option.get("current_contract_price", option.get("mid", "N/A"))}',
                f'• Preferred Entry: ${option.get("preferred_entry_low", "N/A")} – ${option.get("preferred_entry_high", "N/A")}',
                f'• Entry Quality: {option.get("entry_quality", "N/A")}/100',
                f'• Chase Risk: {option.get("chase_risk", "N/A")}',
                f'• Reason: {option.get("watch_reason", "N/A")}',
                f'• Session: {option.get("spx_session", "RTH/Equity")}',
                f'• Cash SPX State: {option.get("cash_spx_state", "N/A")}',
                f'• Previous SPX Reference: {option.get("underlying_reference_price", "N/A")}',
                f'• Indicative SPX Reference: {option.get("indicative_spx_reference", "N/A")}',
                f'• Futures Implied Move: {option.get("futures_implied_move_pct", "N/A")}%'
            ]
            lines += [f'• Entry Diagnostic: {x}' for x in option.get("entry_diagnostics", [])]
            if option.get("spx_session") == "GTH" or option.get("gth_data_diagnostics"):
                lines += ["📡 تفاصيل بيانات SPXW / GTH الفعلية:"] + _gth_diag_lines(option)
        lines += ["🌐 حالة جميع بيانات السياق المستخدمة/المفقودة:"]
        lines += [f'• {x}' for x in _data_context_lines(option)]
    lines += [f'📊 جودة البيانات: {s.get("data_quality", "N/A")}', f'🆔 Trade: {s.get("trade_id", "N/A")}']
    return "\n".join(lines)


def signal_caption(s: dict, max_chars: int = 1024) -> str:
    from app.telegram.message_templates import regular_option_caption
    option = s.get("option") or {}
    if not option:
        full = signal_text(s)
        return full if len(full) <= max_chars else full[:max_chars]
    return regular_option_caption(s, max_chars=max_chars)
