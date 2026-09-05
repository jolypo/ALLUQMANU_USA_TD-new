from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math
import pandas as pd

from app.utils.indicators import add_indicators

NY = ZoneInfo("America/New_York")
RIYADH = ZoneInfo("Asia/Riyadh")


def _num(v, default=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _fmt(v: float | None) -> str:
    return "غير متاح" if v is None else f"${v:,.2f}"


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    x = df.copy()
    for c in ("open", "high", "low", "close", "volume"):
        if c in x.columns:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    if "timestamp" in x.columns:
        x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="coerce")
    return x.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def _aggregate(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    x = _clean(df)
    if x.empty or "timestamp" not in x.columns:
        return pd.DataFrame()
    z = x.set_index("timestamp").sort_index()
    y = z.resample(rule).agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna(subset=["open", "high", "low", "close"])
    y["timestamp"] = y.index
    return y.reset_index(drop=True)


def _pivot_levels(df: pd.DataFrame, current: float, window: int = 2, max_each: int = 2) -> tuple[list[float], list[float]]:
    x = _clean(df)
    if len(x) < 7:
        return [], []
    highs, lows = [], []
    for i in range(window, len(x) - window):
        h = float(x.iloc[i]["high"])
        l = float(x.iloc[i]["low"])
        if h >= float(x.iloc[i-window:i+window+1]["high"].max()):
            highs.append(h)
        if l <= float(x.iloc[i-window:i+window+1]["low"].min()):
            lows.append(l)
    # cluster nearby levels so repeated pivots become one meaningful zone
    def cluster(vals: list[float]) -> list[float]:
        vals = sorted(set(round(v, 4) for v in vals))
        if not vals:
            return []
        tol = max(current * 0.0025, 0.05)
        groups: list[list[float]] = []
        for v in vals:
            if not groups or abs(v - sum(groups[-1])/len(groups[-1])) > tol:
                groups.append([v])
            else:
                groups[-1].append(v)
        return [sum(g)/len(g) for g in groups]
    resist = sorted([v for v in cluster(highs) if v > current], key=lambda v: v-current)[:max_each]
    support = sorted([v for v in cluster(lows) if v < current], key=lambda v: current-v)[:max_each]
    return support, resist


def _fvg(df: pd.DataFrame, current: float) -> dict:
    x = _clean(df)
    if len(x) < 4:
        return {"bullish": None, "bearish": None}
    bull = bear = None
    for i in range(2, len(x)):
        prev2_high = float(x.iloc[i-2]["high"])
        prev2_low = float(x.iloc[i-2]["low"])
        cur_low = float(x.iloc[i]["low"])
        cur_high = float(x.iloc[i]["high"])
        if cur_low > prev2_high:
            zone = (prev2_high, cur_low)
            if zone[0] <= current * 1.03 and zone[1] >= current * 0.97:
                bull = zone
        if cur_high < prev2_low:
            zone = (cur_high, prev2_low)
            if zone[0] <= current * 1.03 and zone[1] >= current * 0.97:
                bear = zone
    return {"bullish": bull, "bearish": bear}


def _order_block(df: pd.DataFrame, direction: str) -> tuple[float, float] | None:
    x = _clean(df)
    if len(x) < 8:
        return None
    recent = x.tail(20).reset_index(drop=True)
    # Last opposite candle before a strong displacement candle (body > 1.4x median body).
    bodies = (recent["close"] - recent["open"]).abs()
    med = float(bodies.median() or 0)
    if med <= 0:
        return None
    for i in range(len(recent)-2, 0, -1):
        nxt_body = abs(float(recent.iloc[i+1]["close"] - recent.iloc[i+1]["open"]))
        row = recent.iloc[i]
        if direction == "bullish" and float(row["close"]) < float(row["open"]) and nxt_body >= 1.4*med and float(recent.iloc[i+1]["close"]) > float(row["high"]):
            return float(row["low"]), float(row["high"])
        if direction == "bearish" and float(row["close"]) > float(row["open"]) and nxt_body >= 1.4*med and float(recent.iloc[i+1]["close"]) < float(row["low"]):
            return float(row["low"]), float(row["high"])
    return None


def _fib(df: pd.DataFrame) -> dict:
    x = _clean(df)
    if len(x) < 10:
        return {}
    z = x.tail(min(80, len(x)))
    hi = float(z["high"].max()); lo = float(z["low"].min())
    if hi <= lo:
        return {}
    # Direction from swing extreme ordering.
    hi_i = int(z["high"].idxmax()); lo_i = int(z["low"].idxmin())
    up = lo_i < hi_i
    span = hi-lo
    if up:
        levels = {r: hi-span*r for r in (0.382,0.5,0.618,0.705)}
        ext = {1.272: lo+span*1.272, 1.618: lo+span*1.618}
    else:
        levels = {r: lo+span*r for r in (0.382,0.5,0.618,0.705)}
        ext = {1.272: hi-span*0.272, 1.618: hi-span*0.618}
    return {"swing_high":hi,"swing_low":lo,"direction":"UP" if up else "DOWN","retracements":levels,"extensions":ext}


def _clock_ar(dt: datetime, zone: ZoneInfo) -> str:
    local = dt.astimezone(zone)
    period = "ص" if local.hour < 12 else "م"
    hour = local.hour % 12 or 12
    return f"{local:%d-%m-%Y} — {hour:02d}:{local:%M:%S} {period}"

def _time_label(dt: datetime) -> dict:
    dt = dt.astimezone(timezone.utc)
    return {"ny": _clock_ar(dt, NY), "riyadh": _clock_ar(dt, RIYADH)}


class StockIntelligenceEngine:
    """Read-only multi-timeframe stock intelligence layer.

    It does not change any trading engine. All levels are derived from market bars;
    unavailable inputs stay unavailable rather than being fabricated.
    """

    async def analyze(self, provider, symbol: str) -> dict:
        detected_at = datetime.now(timezone.utc)
        # Prefer provider-supported native frames. Fall back to aggregation where useful.
        async def get(tf: str, days: int):
            try:
                return _clean(await provider.bars(symbol, tf, days))
            except Exception:
                return pd.DataFrame()

        m15 = await get("15Min", 25)
        h1 = await get("1Hour", 60)
        h4 = await get("4Hour", 150)
        if h4.empty and not h1.empty:
            h4 = _aggregate(h1, "4h")
        d1 = await get("1Day", 420)
        w1 = await get("1Week", 1200)
        if w1.empty and not d1.empty:
            w1 = _aggregate(d1, "W-FRI")
        mo1 = await get("1Month", 2200)
        if mo1.empty and not d1.empty:
            mo1 = _aggregate(d1, "ME")

        base = next((df for df in (m15,h1,h4,d1,w1,mo1) if df is not None and not df.empty), pd.DataFrame())
        if base.empty:
            return {"ok":False,"symbol":symbol,"reason":"MARKET_DATA_UNAVAILABLE"}
        current = float(base.iloc[-1]["close"])

        frames = []
        defs = [
            ("15 دقيقة", "15m", m15, "إغلاق شمعة 15 دقيقة"),
            ("ساعة", "1h", h1, "إغلاق شمعة ساعة"),
            ("4 ساعات", "4h", h4, "إغلاق شمعة 4 ساعات"),
            ("يومي", "1d", d1, "إغلاق يومي"),
            ("أسبوعي", "1w", w1, "إغلاق أسبوعي"),
            ("شهري", "1mo", mo1, "إغلاق شهري"),
        ]
        for ar, key, df, confirm in defs:
            if df.empty:
                frames.append({"name":ar,"key":key,"available":False,"supports":[],"resistances":[],"confirmation":confirm})
                continue
            sup,res = _pivot_levels(df,current,max_each=2)
            frames.append({"name":ar,"key":key,"available":True,"supports":sup,"resistances":res,"confirmation":confirm})

        all_sup = [(current-v, v, f) for f in frames for v in f["supports"] if v < current]
        all_res = [(v-current, v, f) for f in frames for v in f["resistances"] if v > current]
        nearest_support = min(all_sup, default=(None,None,None), key=lambda x:x[0] if x[0] is not None else 1e18)
        nearest_res = min(all_res, default=(None,None,None), key=lambda x:x[0] if x[0] is not None else 1e18)

        daily_ind = add_indicators(d1) if not d1.empty and len(d1) >= 20 else pd.DataFrame()
        intraday_ind = add_indicators(m15) if not m15.empty and len(m15) >= 20 else pd.DataFrame()
        atr_val = _num(daily_ind.iloc[-1].get("atr")) if not daily_ind.empty else None
        rvol = _num(intraday_ind.iloc[-1].get("rvol")) if not intraday_ind.empty else None
        vwap = _num(intraday_ind.iloc[-1].get("vwap")) if not intraday_ind.empty else None
        momentum = _num(intraday_ind.iloc[-1].get("momentum5_pct")) if not intraday_ind.empty else None

        trigger = nearest_res[1]
        trigger_frame = nearest_res[2]["name"] if nearest_res[2] else None
        trigger_confirm = nearest_res[2]["confirmation"] if nearest_res[2] else None
        # The breakout level is not its own target. After the nearest resistance is
        # confirmed, target the next distinct resistance pool above it.
        next_res = sorted([(v, f) for f in frames for v in f["resistances"] if trigger is not None and v > trigger + max(current*0.001, 0.02)], key=lambda x:x[0])
        if next_res:
            target, target_frame_obj = next_res[0]
            target_frame = target_frame_obj["name"]
        else:
            target = trigger
            target_frame = trigger_frame
        horizon = "لحظي" if target_frame in {"15 دقيقة","ساعة"} else "يومي" if target_frame in {"4 ساعات","يومي"} else "أسبوعي" if target_frame=="أسبوعي" else "شهري" if target_frame=="شهري" else None
        if target is not None and atr_val:
            dist = target-current
            plausibility = max(0, min(100, 100 - max(0, dist/atr_val-1.0)*35))
        else:
            plausibility = None

        fib = _fib(h4 if not h4.empty else d1)
        fvg = _fvg(m15 if not m15.empty else h1, current)
        bull_ob = _order_block(m15 if not m15.empty else h1, "bullish")
        bear_ob = _order_block(m15 if not m15.empty else h1, "bearish")

        # ICT-style liquidity is based on recent swing pools, not Level 2.
        buy_side = nearest_res[1]
        sell_side = nearest_support[1]
        trend_score = 50.0
        if not intraday_ind.empty:
            row = intraday_ind.iloc[-1]
            trend_score += 10 if current > _num(row.get("vwap"), current) else -10
            trend_score += 10 if _num(row.get("ema20"), current) < current else -10
            trend_score += max(-10,min(10,(_num(row.get("momentum5_pct"),0) or 0)*4))
        trend_score = max(0,min(100,trend_score))
        trend = "صاعد" if trend_score >= 62 else "هابط" if trend_score <= 38 else "محايد / متوازن"

        return {
            "ok":True,"symbol":symbol,"current":current,"trend":trend,"trend_score":round(trend_score,1),
            "frames":frames,
            "nearest_support":nearest_support[1],"nearest_support_frame":nearest_support[2]["name"] if nearest_support[2] else None,
            "nearest_resistance":trigger,"nearest_resistance_frame":trigger_frame,
            "target":target,"target_horizon":horizon,"target_source_frame":target_frame,"target_confirmation":trigger_confirm,"target_plausibility":None if plausibility is None else round(plausibility,1),
            "atr":atr_val,"rvol":rvol,"vwap":vwap,"momentum5_pct":momentum,
            "ict":{"buy_side":buy_side,"sell_side":sell_side,"bullish_fvg":fvg.get("bullish"),"bearish_fvg":fvg.get("bearish"),"bullish_ob":bull_ob,"bearish_ob":bear_ob},
            "fib":fib,
            "detected_at":detected_at,"detected_time":_time_label(detected_at),
        }

    @staticmethod
    def render_ar(result: dict, sent_at: datetime | None = None) -> str:
        if not result.get("ok"):
            return f"📊 <b>تحليل السهم — {result.get('symbol','')}</b>\n\n❌ بيانات السوق غير متاحة حاليًا."
        sent_at = sent_at or datetime.now(timezone.utc)
        sent = _time_label(sent_at)
        lines = [
            f"📊 <b>تحليل السهم — {result['symbol']}</b>",
            f"💵 <b>السعر الحالي:</b> {_fmt(result['current'])}",
            f"📈 <b>الاتجاه العام:</b> {result['trend']} — {result['trend_score']:.1f}/100",
            "",
            "━━━━━━━━━━━━━━",
        ]
        for idx, f in enumerate(result["frames"]):
            if idx:
                lines.append("---")
            if not f["available"]:
                lines += [f"🕒 <b>فريم {f['name']}:</b> غير متاح"]
                continue
            sup = " | ".join(_fmt(v) for v in f["supports"]) or "غير متاح"
            res = " | ".join(_fmt(v) for v in f["resistances"]) or "غير متاح"
            lines += [
                f"🕒 <b>فريم {f['name']}</b>",
                f"🟢 الدعم: {sup}", f"🔴 المقاومة: {res}",
            ]
            if f["resistances"]:
                lines.append(f"✅ التأكيد: {f['confirmation']} فوق {_fmt(f['resistances'][0])}")
        lines += [
            "", "━━━━━━━━━━━━━━",
            f"📍 <b>أقرب دعم:</b> {_fmt(result['nearest_support'])}" + (f" — {result['nearest_support_frame']}" if result.get('nearest_support_frame') else ""),
            f"📍 <b>أقرب مقاومة:</b> {_fmt(result['nearest_resistance'])}" + (f" — {result['nearest_resistance_frame']}" if result.get('nearest_resistance_frame') else ""),
            f"🎯 <b>الهدف المرجح:</b> {_fmt(result['target'])}" + (f" — هدف {result['target_horizon']}" if result.get('target_horizon') else ""),
        ]
        if result.get("target_confirmation") and result.get("nearest_resistance"):
            lines.append(f"✅ <b>شرط التأكيد:</b> {result['target_confirmation']} فوق {_fmt(result['nearest_resistance'])}")
            if result.get("target") and result.get("target") != result.get("nearest_resistance"):
                lines.append(f"➡️ بعد التأكيد يدعم استهداف {_fmt(result['target'])} ({result.get('target_source_frame') or 'فريم أعلى'})")
        if result.get("target_plausibility") is not None:
            lines.append(f"📐 <b>معقولية الهدف حسب ATR:</b> {result['target_plausibility']:.0f}/100")
        lines += ["", "━━━━━━━━━━━━━━", "🧠 <b>ICT والسيولة</b>"]
        ict = result.get("ict",{})
        lines += [
            f"💧 سيولة شرائية فوق السعر: {_fmt(ict.get('buy_side'))}",
            f"💧 سيولة بيعية تحت السعر: {_fmt(ict.get('sell_side'))}",
            f"📦 Order Block صاعد: {StockIntelligenceEngine._zone(ict.get('bullish_ob'))}",
            f"📦 Order Block هابط: {StockIntelligenceEngine._zone(ict.get('bearish_ob'))}",
            f"⚡ FVG صاعد: {StockIntelligenceEngine._zone(ict.get('bullish_fvg'))}",
            f"⚡ FVG هابط: {StockIntelligenceEngine._zone(ict.get('bearish_fvg'))}",
            "📌 السيولة هنا مشتقة من القمم والقيعان السعرية وليست Level 2/DOM.",
            "", "📐 <b>Fibonacci</b>",
        ]
        fib = result.get("fib") or {}
        if fib:
            for r,v in (fib.get("retracements") or {}).items():
                lines.append(f"• {r:.3f} → {_fmt(v)}")
            for r,v in (fib.get("extensions") or {}).items():
                lines.append(f"🎯 امتداد {r:.3f} → {_fmt(v)}")
        else:
            lines.append("غير متاح")
        lines += [
            "", "📊 <b>الحجم والتذبذب</b>",
            f"🌪 ATR اليومي: {_fmt(result.get('atr'))}",
            "📦 RVOL: " + ("غير متاح" if result.get("rvol") is None else f"{result['rvol']:.2f}x"),
            f"📈 VWAP: {_fmt(result.get('vwap'))}",
            "⚡ زخم 5 شموع: " + ("غير متاح" if result.get("momentum5_pct") is None else f"{result['momentum5_pct']:+.2f}%"),
            "", "⚠️ <b>الفخاخ السعرية</b>",
            "• لا يُعتمد الاختراق بمجرد اللمس؛ يلزم الإغلاق على الفريم المحدد.",
            "• سحب السيولة ثم العودة داخل المستوى يُعامل كاختراق كاذب محتمل.",
            "", "━━━━━━━━━━━━━━",
            "👥 <b>رأي فريق المتداولين الخبراء</b>",
        ]
        trend=result.get("trend") or "محايد / متوازن"
        score=float(result.get("trend_score") or 50)
        plaus=float(result.get("target_plausibility") or 50)
        rvol=result.get("rvol"); mom=result.get("momentum5_pct")
        confidence=max(40,min(92,(abs(score-50)*1.15)+(plaus*0.55)))
        support=result.get("nearest_support"); resistance=result.get("nearest_resistance"); target=result.get("target")
        if trend == "صاعد":
            lines += [
                f"📈 <b>الترجيح:</b> صعود مشروط — ثقة {confidence:.0f}%",
                f"⏱ <b>أول 30 دقيقة بعد الافتتاح:</b> الأفضل الحفاظ فوق {_fmt(support)}؛ اختراق {_fmt(resistance)} على الفريم المحدد يدعم استمرار الصعود.",
                f"🕛 <b>منتصف الجلسة:</b> إذا بقي السعر أعلى VWAP والزخم والحجم داعمين، يصبح {_fmt(target)} الهدف المرجح ({result.get('target_horizon') or 'غير محدد'}).",
                f"❌ <b>إلغاء السيناريو:</b> كسر {_fmt(support)} بإغلاق مؤكد مع ضغط بيعي واضح.",
            ]
        elif trend == "هابط":
            lines += [
                f"📉 <b>الترجيح:</b> هبوط مشروط — ثقة {confidence:.0f}%",
                f"⏱ <b>أول 30 دقيقة بعد الافتتاح:</b> بقاء السعر تحت {_fmt(resistance)} مع ضعف الزخم يدعم اختبار {_fmt(support)}.",
                f"🕛 <b>منتصف الجلسة:</b> استمرار السعر تحت VWAP والحجم البيعي يرفع احتمال امتداد الهبوط بعد كسر {_fmt(support)}.",
                f"❌ <b>إلغاء السيناريو:</b> استعادة {_fmt(resistance)} بإغلاق مؤكد وحجم داعم.",
            ]
        else:
            lines += [
                f"⚖️ <b>الترجيح:</b> توازن وانتظار كسر — ثقة {confidence:.0f}%",
                f"⏱ <b>أول 30 دقيقة بعد الافتتاح:</b> النطاق بين {_fmt(support)} و {_fmt(resistance)} هو منطقة القرار؛ لا أفضلية قبل كسر مؤكد.",
                f"🕛 <b>منتصف الجلسة:</b> الاختراق فوق المقاومة يدعم {_fmt(target)}، بينما كسر الدعم يرجح انتقال السيناريو إلى سلبي.",
            ]
        if rvol is not None or mom is not None:
            lines.append("📊 <b>دعم القرار:</b> " + (f"RVOL {float(rvol):.2f}x" if rvol is not None else "RVOL غير متاح") + " | " + (f"زخم {float(mom):+.2f}%" if mom is not None else "الزخم غير متاح"))
        lines += [
            "📌 هذا رأي احتمالي ناتج من توافق الفريمات + ICT + Fibonacci + ATR + VWAP + الحجم والسيولة، وليس ضمانًا لوقت أو سعر محدد.",
            "", "━━━━━━━━━━━━━━",
            "🕒 <b>وقت اكتشاف التحليل</b>",
            f"🗽 نيويورك: {result['detected_time']['ny']}",
            f"🇸🇦 الرياض: {result['detected_time']['riyadh']}",
            "📨 <b>وقت إرسال الرسالة</b>",
            f"🗽 نيويورك: {sent['ny']}",
            f"🇸🇦 الرياض: {sent['riyadh']}",
        ]
        return "\n".join(("\u200f" + line) if line else "" for line in lines)

    @staticmethod
    def _zone(z) -> str:
        if not z or len(z) != 2:
            return "غير متاح"
        return f"{_fmt(float(z[0]))} – {_fmt(float(z[1]))}"
