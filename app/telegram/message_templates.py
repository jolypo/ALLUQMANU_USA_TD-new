from __future__ import annotations

from datetime import datetime, timezone, timedelta
from html import escape
from zoneinfo import ZoneInfo

RLM = "\u200f"
NY = ZoneInfo("America/New_York")
RIYADH = ZoneInfo("Asia/Riyadh")

AR_MONTHS = {
    1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل", 5: "مايو", 6: "يونيو",
    7: "يوليو", 8: "أغسطس", 9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
}


def _e(v) -> str:
    return escape("غير متاح" if v is None or v == "" else str(v))


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def money(v) -> str:
    x = _f(v)
    return "غير متاح" if x is None else f"${x:,.2f}"


def num(v, digits=1) -> str:
    x = _f(v)
    return "غير متاح" if x is None else f"{x:.{digits}f}"


def parse_dt(v):
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def ar_datetime(dt: datetime | None, tz: ZoneInfo, with_date=True) -> str:
    if not dt:
        return "غير متاح"
    local = dt.astimezone(tz)
    h = local.strftime("%I").lstrip("0") or "12"
    period = "صباحًا" if local.hour < 12 else "مساءً"
    time_text = f"{h}:{local.strftime('%M:%S')} {period}"
    if not with_date:
        return time_text
    return f"{local.day:02d} {AR_MONTHS[local.month]} {local.year} — {time_text}"


def engine_label(option: dict) -> str:
    mode = str(option.get("strategy_mode") or option.get("engine_source") or "").upper().replace(" ", "_")
    labels = {
        "WASEEM_V1": "وسيم V1", "WASEEM_V2": "وسيم V2", "WASEEM_V3": "وسيم V3",
        "WASEEM_V4": "وسيم V4", "WASEEM_V5": "وسيم V5", "WASEEM_V6": "وسيم V6", "SPX_V20": "SPX V20",
        "SPX_CORE": "SPX Core", "CONFIRMED_SETUP": "Confirmed Setup",
    }
    return labels.get(mode, str(option.get("engine_source") or option.get("strategy_mode") or "Core"))


def option_type(option: dict) -> str:
    typ = str(option.get("type") or option.get("option_type") or "OPTION").upper()
    return "CALL" if typ in {"C", "CALL"} else "PUT" if typ in {"P", "PUT"} else typ


def strength_label(score) -> str:
    x = _f(score)
    if x is None:
        return "غير متاح"
    if x >= 96:
        return "استثنائية جدًا"
    if x >= 92:
        return "استثنائية"
    if x >= 88:
        return "قوية جدًا"
    if x >= 83:
        return "قوية"
    if x >= 75:
        return "جيدة"
    return "محدودة"




def ar_state(v) -> str:
    raw = "غير متاح" if v is None or v == "" else str(v)
    m = {
        "HIGH":"مرتفعة", "MEDIUM":"متوسطة", "LOW":"منخفضة", "NORMAL":"طبيعي",
        "AVAILABLE":"متاح", "UNAVAILABLE":"غير متاح", "DELAYED":"متأخر", "PARTIAL":"جزئي", "STALE":"قديم",
        "BUYING":"ضغط شراء", "SELLING":"ضغط بيع", "BALANCED":"متوازن",
        "TOWARD_ASK":"نحو Ask", "TOWARD_BID":"نحو Bid", "POSITIVE":"إيجابي", "NEGATIVE":"سلبي", "NEUTRAL":"محايد",
        "LOW":"منخفض", "MEDIUM":"متوسط", "ENTRY_READY":"جاهز للدخول", "KEEP_WATCH":"تحت المراقبة",
        "WATCH_TO_READY":"WATCH → READY", "TRUE":"نعم", "FALSE":"لا",
    }
    return m.get(raw.upper(), raw)

def horizon_label(option: dict) -> str:
    h = str(option.get("horizon") or option.get("dte_mode") or "").upper()
    if h in {"WEEKLY", "1-7DTE", "1–7DTE"}:
        return "أسبوعي WEEKLY"
    if h in {"DAILY", "0DTE"}:
        return "يومي 0DTE"
    if h == "MONTHLY":
        return "شهري MONTHLY"
    return h or "غير متاح"


def rtl(lines: list[str]) -> str:
    return "\n".join((RLM + line) if line else "" for line in lines)


def regular_option_caption(trade: dict, max_chars: int = 1000) -> str:
    option = trade.get("option") or {}
    detected = parse_dt(trade.get("_candidate_detected_at") or trade.get("detected_at") or trade.get("published_at") or trade.get("created_at"))
    now = datetime.now(timezone.utc)
    symbol = _e(trade.get("symbol", "غير متاح"))
    score = trade.get("score")
    required = trade.get("required_score", "غير متاح")
    current = option.get("current_contract_price", option.get("mid"))
    zone_lo = option.get("underlying_entry_low", trade.get("current_price"))
    zone_hi = option.get("underlying_entry_high", trade.get("current_price"))
    lines = [
        "🚨 <b>فرصة عقود جديدة</b>", "",
        f"🏢 <b>السهم:</b> {symbol}",
        f"📈 <b>نوع الصفقة:</b> {_e(option_type(option))}",
        f"🎯 <b>Strike:</b> {_e(option.get('strike'))}",
        f"📅 <b>تاريخ الانتهاء:</b> {_e(option.get('expiration'))}",
        f"⏳ <b>المتبقي للانتهاء:</b> {_e(option.get('dte'))} أيام",
        f"🗓 <b>نوع العقد:</b> {_e(horizon_label(option))}", "",
        f"🔥 <b>قوة الإشارة:</b> {_e(score)} / 100",
        f"🧠 <b>المحرك:</b> {_e(engine_label(option))}",
        f"⭐ <b>التقييم:</b> {_e(strength_label(score))}",
        f"🎯 <b>القوة المطلوبة:</b> {_e(required)} / 100", "",
        f"💵 <b>سعر العقد الحالي:</b> {money(current)}",
        f"💰 <b>منطقة الدخول:</b> {money(trade.get('entry_low'))} – {money(trade.get('entry_high'))}",
        f"🛑 <b>وقف الخسارة:</b> {money(trade.get('stop'))}",
        f"🎯 <b>الأهداف:</b> TP1 {money(trade.get('tp1'))} | TP2 {money(trade.get('tp2'))} | TP3 {money(trade.get('tp3'))}", "",
        "---", "",
        f"📍 <b>منطقة السهم:</b> {money(zone_lo)} – {money(zone_hi)}",
        f"🇸🇦 <b>اكتشاف العقد:</b> {_e(ar_datetime(detected, RIYADH))}",
        f"🇸🇦 <b>الوقت الحالي:</b> {_e(ar_datetime(now, RIYADH))}",
        f"🆔 <b>رقم الفرصة:</b> {_e(trade.get('trade_id', 'غير متاح'))}",
    ]
    text = rtl(lines)
    return text if len(text) <= max_chars else text[:max_chars]


def _context_ar(line: str) -> str:
    s = str(line or "")
    repl = {
        "AVAILABLE": "متاح", "UNAVAILABLE": "غير متاح", "DELAYED": "متأخر", "PARTIAL": "جزئي", "STALE": "قديم",
        "BULLISH": "إيجابي", "BEARISH": "سلبي", "NEUTRAL": "محايد", "CAUTION": "تنبيه",
        "Economic Calendar": "الأحداث الاقتصادية", "Earnings": "الأرباح", "NEWS": "آخر الأخبار",
        "age": "العمر", "none in 3-month calendar": "لا توجد أرباح ضمن التقويم المتاح",
    }
    for a, b in repl.items():
        s = s.replace(a, b)
    return s


def candidate_full_text(trade: dict, candidate_ttl_seconds: int, detected_at: datetime | None = None) -> str:
    detected = detected_at or datetime.now(timezone.utc)
    if detected.tzinfo is None:
        detected = detected.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    option = trade.get("option") or {}
    mode = str(option.get("strategy_mode") or "").upper()
    decision = str(trade.get("decision") or "READY").upper()
    transition = trade.get("v6_watch_transition") or trade.get("v5_watch_transition") or trade.get("v4_watch_transition")
    if transition == "WATCH_TO_READY":
        title = "✅ <b>تحولت الفرصة إلى جاهزة — ENTRY READY</b>"
    elif decision == "WATCH":
        title = "👁 <b>الفرصة تحت المراقبة — WATCH</b>"
    elif decision == "WAIT":
        title = "⏳ <b>الفرصة قيد الانتظار — WAIT</b>"
    elif str(trade.get("market_state") or "").upper() == "WASEEM_V6_NO_TRADE":
        title = "⛔ <b>لا توجد صفقة مناسبة — NO TRADE</b>"
    elif decision == "REJECT":
        title = "❌ <b>الفرصة مرفوضة — REJECT</b>"
    else:
        title = "🔥 <b>فرصة جاهزة — READY</b>"

    if mode == "WASEEM_V6" and option.get("v6_premarket_plan"):
        cross_map={"POSITIVE_CROSS":"تقاطع موجب","NEGATIVE_CROSS":"تقاطع سالب","POSITIVE":"إيجابي","NEGATIVE":"سلبي","NEUTRAL":"محايد","UNAVAILABLE":"غير متاح"}
        ict=option.get("v6_ict") or {}; fib=option.get("v6_fib") or {}
        session_label = str(option.get("v6_session") or "PREMARKET").upper()
        plan_title = "🌅 <b>خطة وسيم V6 قبل الافتتاح — PRE-MARKET WATCH</b>" if session_label == "PREMARKET" else "🌙 <b>خطة وسيم V6 خارج الجلسة الرسمية — CONTEXT WATCH</b>"
        lines=[
            plan_title, "",
            "🧠 <b>المحرك:</b> وسيم V6",
            f"🔥 <b>قوة السيناريو:</b> {_e(option.get('v6_plan_confidence',trade.get('score')))} / 100",
            f"🏢 <b>السهم:</b> {_e(trade.get('symbol'))}",
            f"📈 <b>الاتجاه المرجح:</b> {_e(option.get('v6_plan_direction',trade.get('direction')))}",
            f"💵 <b>السعر الحالي:</b> {money(trade.get('current_price'))}",
            f"🟢 <b>الدعم الأقرب:</b> {money(option.get('v6_nearest_support'))}",
            f"🔴 <b>المقاومة الأقرب:</b> {money(option.get('v6_nearest_resistance'))}",
            f"✅ <b>تأكيد ما بعد الافتتاح:</b> {_e(option.get('v6_target_confirmation') or 'إعادة فحص البنية والحجم بعد الافتتاح')}",
            f"🎯 <b>الهدف المحتمل للسهم:</b> {money(option.get('v6_next_target'))}",
            f"🗓 <b>نوع الهدف:</b> {_e(option.get('v6_target_horizon') or 'غير متاح')}",
            f"🛑 <b>إلغاء السيناريو عند:</b> {money(option.get('v6_invalidation_level'))}",
            "", "---", "",
            "🧠 <b>طبقات V6</b>",
            f"💧 <b>Buy-Side Liquidity:</b> {money(ict.get('buy_side'))}",
            f"💧 <b>Sell-Side Liquidity:</b> {money(ict.get('sell_side'))}",
            f"📦 <b>Order Block صاعد:</b> {_e(ict.get('bullish_ob') or 'غير متاح')}",
            f"📦 <b>Order Block هابط:</b> {_e(ict.get('bearish_ob') or 'غير متاح')}",
            f"⚡ <b>FVG صاعد:</b> {_e(ict.get('bullish_fvg') or 'غير متاح')}",
            f"⚡ <b>FVG هابط:</b> {_e(ict.get('bearish_fvg') or 'غير متاح')}",
            f"📐 <b>Fibonacci:</b> {_e(fib.get('direction') or 'غير متاح')}",
            f"➕➖ <b>Cross:</b> {_e(cross_map.get(str(option.get('v6_cross_state')),str(option.get('v6_cross_state') or 'غير متاح')))}",
            f"📊 <b>RVOL:</b> {_e(option.get('v6_rvol'))}",
            f"🌪 <b>ATR:</b> {money(option.get('v6_atr'))}",
            f"📈 <b>VWAP:</b> {money(option.get('v6_vwap'))}",
            f"🚀 <b>Momentum:</b> {_e(option.get('v6_momentum5_pct'))}%",
            "", "---", "",
            "👥 <b>رأي فريق المتداولين الخبراء</b>",
            "الخطة الحالية للسهم فقط وليست أمر شراء عقد. بعد افتتاح السوق يعيد V6 فحص الاتجاه والحجم وICT/Fibonacci ثم يفحص Option Chain والـSpread وVolume/OI وDelta/Gamma/Theta/Vega وIV قبل اختيار العقد النهائي.",
            "📌 إذا لم يتأكد السيناريو بعد الافتتاح تبقى WATCH أو تتحول NO TRADE.",
            "",
            f"🕒 <b>وقت بناء الخطة — نيويورك:</b> {_e(ar_datetime(detected, NY))}",
            f"🇸🇦 <b>وقت بناء الخطة — الرياض:</b> {_e(ar_datetime(detected, RIYADH))}",
            f"🇸🇦 <b>الوقت الحالي — الرياض:</b> {_e(ar_datetime(now, RIYADH))}",
        ]
        return rtl(lines)

    data_ts = parse_dt(option.get("quote_timestamp") or option.get("underlying_data_timestamp") or trade.get("market_timestamp"))
    expires = detected + timedelta(seconds=int(candidate_ttl_seconds))
    score = trade.get("score")
    lines = [title, "",
        f"🧠 <b>المحرك:</b> {_e(engine_label(option))}",
        f"🔥 <b>قوة الإشارة:</b> {_e(score)} / 100",
        f"🏢 <b>السهم:</b> {_e(trade.get('symbol'))}",
        f"📈 <b>نوع الصفقة:</b> {_e(option_type(option))}",
        f"🎯 <b>Strike:</b> {_e(option.get('strike'))}",
        f"📅 <b>تاريخ الانتهاء:</b> {_e(option.get('expiration'))}",
        f"⏳ <b>المتبقي للانتهاء:</b> {_e(option.get('dte'))} أيام",
        f"🗓 <b>نوع العقد:</b> {_e(horizon_label(option))}",
        f"⭐ <b>التقييم:</b> {_e(strength_label(score))}",
        f"🎯 <b>القوة المطلوبة:</b> {_e(trade.get('required_score'))} / 100", "",
        f"💵 <b>سعر العقد الحالي:</b> {money(option.get('current_contract_price', option.get('mid')))}",
        f"💰 <b>منطقة الدخول:</b> {money(trade.get('entry_low'))} – {money(trade.get('entry_high'))}",
        f"🛑 <b>وقف الخسارة:</b> {money(trade.get('stop'))}",
        f"🎯 <b>الأهداف:</b> TP1 {money(trade.get('tp1'))} | TP2 {money(trade.get('tp2'))} | TP3 {money(trade.get('tp3'))}", "",
        "---", "",
        f"🌐 <b>بوابة السوق:</b> {_e(trade.get('market_state', 'NORMAL'))}",
        f"💧 <b>السيولة:</b> {_e(ar_state(trade.get('liquidity_state', 'NORMAL')))}",
        f"🌪 <b>التذبذب:</b> {_e(ar_state(trade.get('volatility_state', 'NORMAL')))}",
        f"⚖️ <b>العائد مقابل المخاطرة:</b> 1:{_e(trade.get('rr'))}",
        f"📐 <b>الحركة المتوقعة:</b> {money(option.get('expected_move'))}",
        f"📍 <b>بعد Strike عن السعر:</b> {_e(option.get('strike_distance'))}",
        f"📊 <b>تغطية الحركة المتوقعة:</b> {_e(option.get('expected_move_coverage'))}x",
        f"⚡ <b>كفاءة Strike:</b> {_e(option.get('strike_efficiency'))}",
        f"📦 <b>حجم Bid / Ask:</b> {_e(option.get('bid_size'))} / {_e(option.get('ask_size'))}",
        f"💵 <b>Bid:</b> {money(option.get('bid'))} | <b>Ask:</b> {money(option.get('ask'))} | <b>Mid:</b> {money(option.get('mid'))}",
        f"📈 <b>سعر السهم الحالي:</b> {money(option.get('underlying_current_price', trade.get('current_price')))}",
        f"📡 <b>عمر بيانات العقد:</b> {_e(option.get('quote_age_minutes'))} دقيقة",
        f"📡 <b>عمر بيانات السهم:</b> {_e(option.get('underlying_data_age_minutes', trade.get('market_age_minutes')))} دقيقة",
    ]

    if mode in {"WASEEM_V3", "WASEEM_V4", "WASEEM_V5", "WASEEM_V6"}:
        lines += ["", "📊 <b>جودة الدخول والمراقبة</b>",
            f"📈 <b>جودة الإعداد:</b> {_e(option.get('setup_score', trade.get('score')))} / 100",
            f"📄 <b>جودة العقد:</b> {_e(option.get('contract_score'))} / 100",
            f"🎯 <b>جودة الدخول:</b> {_e(option.get('entry_quality'))} / 100",
            f"👁 <b>حالة الدخول:</b> {_e(ar_state(option.get('entry_state')))}",
            f"💰 <b>الدخول المفضل:</b> {money(option.get('preferred_entry_low'))} – {money(option.get('preferred_entry_high'))}",
            f"⚠️ <b>خطر المطاردة:</b> {_e(ar_state(option.get('chase_risk')))}",
            f"📌 <b>سبب WATCH/الدخول:</b> {_e(option.get('watch_reason'))}",
            f"🔄 <b>انتقال المراقبة:</b> {_e(ar_state(transition))}",
            f"🕒 <b>أول اكتشاف:</b> {_e(ar_datetime(parse_dt(trade.get('first_detected_at')), RIYADH))}",
            f"👁 <b>إضافة للمراقبة:</b> {_e(ar_datetime(parse_dt(trade.get('watch_added_at')), RIYADH))}",
            f"✅ <b>أصبح جاهزًا:</b> {_e(ar_datetime(parse_dt(trade.get('entry_ready_at')), RIYADH))}",
        ]

    if mode in {"WASEEM_V4", "WASEEM_V5", "WASEEM_V6"}:
        lines += ["", "📊 <b>تحليل OHLCV والسيولة</b>",
            f"💧 <b>خريطة السيولة:</b> {_e(option.get('v4_liquidity_score'))} / 100",
            f"⚡ <b>Pre-Move:</b> {_e(option.get('v4_pre_move_score'))} / 100",
            f"💧 <b>السيولة الداخلية:</b> {_e(option.get('v4_internal_liquidity'))}",
            f"🌐 <b>السيولة الخارجية:</b> {_e(option.get('v4_external_liquidity'))}",
            f"📊 <b>كثافة السيولة:</b> {_e(option.get('v4_liquidity_density'))} / 100",
            f"⚡ <b>تسارع الحجم:</b> {_e(option.get('v4_volume_acceleration'))} / 100",
            f"🚀 <b>تسارع الزخم:</b> {_e(option.get('v4_momentum_acceleration'))} / 100",
            f"📦 <b>ضغط النطاق:</b> {_e(option.get('v4_compression'))} / 100",
            "📈 <b>4 مستويات فوق السعر:</b> " + _e(option.get("ohlcv_levels_above", "غير متاح")),
            f"➡️ <b>السعر الحالي:</b> {money(option.get('underlying_current_price', trade.get('current_price')))}",
            "📉 <b>4 مستويات تحت السعر:</b> " + _e(option.get("ohlcv_levels_below", "غير متاح")),
        ]

    if mode == "WASEEM_V5":
        def v5(name):
            return option.get(name) if option.get(name) is not None else "غير متاح"
        lines += ["", "🌊 <b>Order Flow — وسيم V5</b>",
            f"📊 <b>V5 Score:</b> {_e(v5('v5_score'))} / 100",
            f"🌊 <b>Order Flow Score:</b> {_e(v5('v5_order_flow_score'))} / 100",
            f"🎯 <b>ثقة التدفق:</b> {_e(ar_state(v5('v5_flow_confidence')))}",
            f"🟢 <b>ضغط Bid/Ask:</b> {_e(ar_state(v5('v5_bid_ask_pressure')))}",
            f"⚡ <b>اتجاه تنفيذ الصفقات:</b> {_e(ar_state(v5('v5_trade_aggression')))}",
            f"📈 <b>ضغط التنفيذ:</b> {_e(ar_state(v5('v5_execution_pressure')))}",
            f"⚖️ <b>عدم توازن دفتر الأوامر:</b> {_e(ar_state(v5('v5_book_imbalance')))}",
            f"🧲 <b>الامتصاص:</b> {_e(ar_state(v5('v5_absorption')))}",
            f"🔄 <b>إعادة تعبئة السيولة:</b> {_e(ar_state(v5('v5_replenishment')))}",
            f"📡 <b>حالة Quote:</b> {_e(ar_state(v5('v5_quote_status')))} | <b>حالة Trade:</b> {_e(ar_state(v5('v5_trade_status')))}",
        ]

    if mode == "WASEEM_V6":
        def v5base(name):
            return option.get(name) if option.get(name) is not None else "غير متاح"
        lines += ["", "🌊 <b>Order Flow — طبقة مساندة</b>",
            f"🌊 <b>Order Flow Score:</b> {_e(v5base('v5_order_flow_score'))} / 100",
            f"🎯 <b>ثقة التدفق:</b> {_e(ar_state(v5base('v5_flow_confidence')))}",
            "📌 مع البيانات المتأخرة يخفض V6 وزن Order Flow تلقائيًا.",
        ]

    if mode == "WASEEM_V6":
        def v6(name):
            return option.get(name) if option.get(name) is not None else "غير متاح"
        cross_map={"POSITIVE_CROSS":"تقاطع موجب","NEGATIVE_CROSS":"تقاطع سالب","POSITIVE":"إيجابي","NEGATIVE":"سلبي","NEUTRAL":"محايد","UNAVAILABLE":"غير متاح"}
        lines += ["", "🛡️ <b>تحليل وسيم V6 — منع نهاية الزخم</b>",
            f"📊 <b>V6 Score:</b> {_e(v6('v6_score'))} / 100",
            f"🕒 <b>جلسة السوق:</b> {_e(ar_state(v6('v6_session')))}",
            f"📡 <b>البيانات متأخرة/محدودة:</b> {'نعم' if option.get('v6_delayed_data') else 'لا'}",
            f"📡 <b>Feed الأسهم / الخيارات:</b> {_e(v6('v6_stock_feed'))} / {_e(v6('v6_options_feed'))}",
            f"🧭 <b>توافق الفريمات:</b> {_e(v6('v6_multi_timeframe_score'))} / 100",
            f"🎯 <b>المساحة حتى الهدف:</b> {_e(v6('v6_room_to_target_score'))} / 100",
            f"🚀 <b>استمرار/تلاشي الزخم:</b> {_e(v6('v6_momentum_decay_score'))} / 100",
            f"⏰ <b>جودة توقيت الدخول:</b> {_e(v6('v6_late_entry_score'))} / 100",
            f"⚡ <b>جودة الاختراق:</b> {_e(v6('v6_breakout_quality_score'))} / 100",
            f"🔄 <b>خطر الانعكاس:</b> {_e(v6('v6_reversal_risk_score'))} / 100",
            f"🧠 <b>ICT:</b> {_e(v6('v6_ict_score'))} / 100",
            f"📐 <b>Fibonacci:</b> {_e(v6('v6_fibonacci_score'))} / 100",
            f"➕➖ <b>Cross:</b> {_e(cross_map.get(str(v6('v6_cross_state')), str(v6('v6_cross_state'))))} — {_e(v6('v6_cross_score'))} / 100",
            f"📍 <b>أقرب دعم للسهم:</b> {money(option.get('v6_nearest_support'))}",
            f"📍 <b>أقرب مقاومة للسهم:</b> {money(option.get('v6_nearest_resistance'))}",
            f"🎯 <b>الهدف الهيكلي التالي للسهم:</b> {money(option.get('v6_next_target'))}",
            f"📈 <b>الحركة المتوقعة للسهم نحو الهدف:</b> {money(option.get('v6_projected_underlying_move'))}",
            f"💵 <b>سعر العقد المتوقع عند الهدف تقريبًا:</b> {money(option.get('v6_projected_contract_price'))}",
            f"📊 <b>الارتفاع النظري للعقد:</b> {_e(option.get('v6_projected_contract_gain_pct'))}%",
            f"✅ <b>تأكيد العقد بعد الافتتاح:</b> {'نعم' if option.get('v6_contract_confirmed_after_open') else 'لا'}",
            f"📌 <b>مرحلة V6:</b> {_e(option.get('v6_phase','CONTRACT_CONFIRMATION'))}",
            "", "📄 <b>فحص العقد التنفيذي — V6</b>",
            f"📊 <b>جودة العقد:</b> {_e(option.get('contract_score','غير متاح'))} / 100",
            f"💵 <b>Spread:</b> {_e(option.get('spread_pct', option.get('spread_percent','غير متاح')))}%",
            f"📦 <b>Volume:</b> {_e(option.get('volume','غير متاح'))} | <b>OI:</b> {_e(option.get('open_interest', option.get('oi','غير متاح')))}",
            f"Δ <b>Delta:</b> {_e(option.get('delta','غير متاح'))} | Γ <b>Gamma:</b> {_e(option.get('gamma','غير متاح'))}",
            f"Θ <b>Theta:</b> {_e(option.get('theta','غير متاح'))} | Vega <b>Vega:</b> {_e(option.get('vega','غير متاح'))}",
            f"🌪 <b>IV:</b> {_e(option.get('iv', option.get('implied_volatility','غير متاح')))}",
            f"📌 <b>قرار V6:</b> {_e(option.get('v6_watch_reason'))}",
        ]

    ctx = list(option.get("market_context_lines") or [])
    if ctx:
        lines += ["", "🌐 <b>حالة السوق والبيانات</b>"]
        lines += [f"• {_e(_context_ar(row))}" for row in ctx]
        headline = next((str(x).split("—", 1)[1].strip() for x in ctx if str(x).upper().startswith("NEWS:") and "—" in str(x)), None)
        if headline:
            impact = option.get("news_impact_pct")
            direction = option.get("news_impact_direction")
            lines += [f"📰 <b>آخر الأخبار:</b> {_e(headline)}",
                      f"🧠 <b>تأثير الخبر على السهم:</b> {_e(direction)} {_e(impact)}%" if impact is not None else "🧠 <b>تأثير الخبر على السهم:</b> غير متاح"]

    lag = "غير متاح"
    if data_ts:
        lag_seconds = max(0, int((detected.astimezone(timezone.utc) - data_ts.astimezone(timezone.utc)).total_seconds()))
        lag = f"{lag_seconds // 60} دقيقة و{lag_seconds % 60} ثانية"
    lines += ["", "---", "",
        f"🕒 <b>اكتشاف العقد — نيويورك:</b> {_e(ar_datetime(detected, NY))}",
        f"🇸🇦 <b>اكتشاف العقد — الرياض:</b> {_e(ar_datetime(detected, RIYADH))}",
        f"🕒 <b>وقت بيانات السوق — نيويورك:</b> {_e(ar_datetime(data_ts, NY))}",
        f"🇸🇦 <b>وقت بيانات السوق — الرياض:</b> {_e(ar_datetime(data_ts, RIYADH))}",
        f"🇸🇦 <b>الوقت الحالي — الرياض:</b> {_e(ar_datetime(now, RIYADH))}",
        f"⏱ <b>فرق الاكتشاف عن البيانات:</b> {_e(lag)}",
        f"⏳ <b>صلاحية الفرصة:</b> {int(candidate_ttl_seconds)//60} دقائق",
        f"⌛ <b>انتهاء صلاحية الدخول:</b> {_e(ar_datetime(expires, RIYADH))}",
    ]
    return rtl(lines)


def success_message(trade: dict, entry: float, price: float, threshold: float, usd: float, sar: float, momentum_icon: str, momentum_state: str, advice: str) -> str:
    option = trade.get("option") or {}
    return rtl([
        "✅ <b>تم تسجيل الإشارة كناجحة</b>",
        f"🏢 <b>{_e(trade.get('symbol'))}   |   {_e(option_type(option))}   |   Strike {_e(option.get('strike'))}</b>",
        f"💰 <b>الدخول:</b> {money(entry)} | 📈 <b>العقد الآن:</b> {money(price)}",
        f"🎯 <b>حد النجاح:</b> +${threshold:,.2f}",
        f"💵 <b>الربح الحالي بالدولار:</b> +${usd:,.2f}",
        f"🇸🇦 <b>بالريال السعودي:</b> +{sar:,.2f} ريال",
        f"{_e(momentum_icon)} <b>الزخم:</b> {_e(momentum_state)} — {_e(advice)}",
        "🛡️ <b>حماية الربح:</b> مفعلة" if (_f(trade.get("stop")) is not None and _f(trade.get("stop")) >= entry) else "🛡️ <b>حماية الربح:</b> البيع المتحرك غير مفعّل بعد",
        "📌 نجاح الإشارة إحصائيًا لا يعني إغلاق الصفقة.",
    ])


def entry_message(trade: dict, fill: float) -> str:
    option = trade.get("option") or {}
    return rtl([
        "✅ <b>تم الدخول في الصفقة</b>", "",
        f"🏢 <b>{_e(trade.get('symbol'))}   |   {_e(option_type(option))}   |   Strike {_e(option.get('strike'))}</b>", "",
        f"💰 <b>سعر الدخول:</b>   {money(fill)}", "",
        f"🆔 <b>{_e(trade.get('trade_id'))}</b>",
    ])


def profit_update_message(trade: dict, entry: float, price: float, usd: float, sar: float, now: datetime | None = None) -> str:
    option = trade.get("option") or {}
    now = now or datetime.now(timezone.utc)
    pnl = ((price-entry)/entry*100) if entry > 0 else 0.0
    return rtl([
        "📈 <b>تحديث الأرباح</b>", "",
        f"🏢 <b>{_e(trade.get('symbol'))}   |   {_e(option_type(option))}   |   Strike {_e(option.get('strike'))}</b>", "",
        f"💵 <b>الدخول:</b> {money(entry)}   |   <b>العقد الآن:</b> {money(price)}",
        f"📊 <b>نسبة الربح:</b> {pnl:+.2f}%",
        f"💰 <b>الربح بالدولار:</b> {usd:+.2f}$",
        f"🇸🇦 <b>الربح بالريال السعودي:</b> {sar:+.2f} ريال", "",
        f"🕒 <b>السعودية:</b> {_e(ar_datetime(now, RIYADH, with_date=False))}",
        f"🗽 <b>نيويورك:</b> {_e(ar_datetime(now, NY, with_date=False))}", "",
        f"🆔 <b>{_e(trade.get('trade_id'))}</b>",
    ])
