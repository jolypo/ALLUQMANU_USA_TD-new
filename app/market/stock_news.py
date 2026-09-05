from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import re
import math
import html
import os
import httpx
import pandas as pd
from app.utils.indicators import add_indicators

NY=ZoneInfo("America/New_York"); RIYADH=ZoneInfo("Asia/Riyadh")

POS=("beat","beats","raises guidance","raise guidance","upgrade","upgraded","wins","contract","approval","approved","buyback","dividend increase","strong demand","partnership","record revenue")
NEG=("miss","misses","cuts guidance","cut guidance","downgrade","downgraded","lawsuit","probe","investigation","recall","offering","dilution","weak demand","bankruptcy")

_TRANSLATION_CACHE: dict[str, str] = {}

def _looks_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" for ch in (text or ""))

async def _translate_ar(text: str) -> str:
    """Best-effort Arabic translation for Telegram display.

    Uses Google's public translate endpoint without storing credentials. If the
    endpoint is unavailable, the original source text is preserved rather than
    fabricating a translation. Tests can disable outbound translation with
    NEWS_TRANSLATION_DISABLE=1.
    """
    text=(text or "").strip()
    if not text or _looks_arabic(text):
        return text
    if text in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[text]
    if str(os.getenv("NEWS_TRANSLATION_DISABLE", "")).strip().lower() in {"1","true","yes","on"}:
        return text
    try:
        async with httpx.AsyncClient(timeout=3.5) as client:
            resp=await client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client":"gtx","sl":"auto","tl":"ar","dt":"t","q":text},
            )
            resp.raise_for_status()
            data=resp.json()
            translated="".join(str(part[0]) for part in (data[0] or []) if part and part[0]).strip()
            if translated:
                _TRANSLATION_CACHE[text]=translated
                return translated
    except Exception:
        pass
    return text


def _clock_ar(dt, zone):
    local=dt.astimezone(zone); period="ص" if local.hour<12 else "م"; hour=local.hour%12 or 12
    return f"{local:%d-%m-%Y} — {hour:02d}:{local:%M:%S} {period}"

def _time(dt):
    return {"ny":_clock_ar(dt,NY),"riyadh":_clock_ar(dt,RIYADH)}

def _f(v):
    return "غير متاح" if v is None else f"${v:,.2f}"

def _news_type(text:str)->str:
    t=text.lower()
    if any(k in t for k in ("earnings","eps","revenue","quarter","guidance")): return "نتائج أرباح / توجيه"
    if any(k in t for k in ("contract","agreement","deal","award")): return "عقد / اتفاقية"
    if "dividend" in t: return "توزيعات أرباح"
    if any(k in t for k in ("buyback","repurchase")): return "إعادة شراء أسهم"
    if any(k in t for k in ("upgrade","downgrade","price target","rating")): return "توصية محللين"
    if any(k in t for k in ("fda","approval","regulatory")): return "تنظيمي / موافقة"
    return "خبر تشغيلي / سوقي"

def _sentiment(text:str)->tuple[str,float]:
    t=text.lower(); p=sum(k in t for k in POS); n=sum(k in t for k in NEG)
    raw=p-n
    if raw>0: return "إيجابي", min(95,62+raw*9)
    if raw<0: return "سلبي", min(95,62+abs(raw)*9)
    return "محايد / يحتاج تأكيد السعر", 50.0

class StockNewsEngine:
    async def analyze(self, provider, symbol:str, economic=None)->dict:
        detected=datetime.now(timezone.utc)
        try: rows=await provider.news(symbol,24,8)
        except Exception: rows=[]
        try: bars=await provider.bars(symbol,"1Day",60)
        except Exception: bars=pd.DataFrame()
        try: spy_bars=await provider.bars("SPY","1Day",15)
        except Exception: spy_bars=pd.DataFrame()
        current=atr=None; five_day=None; market_5d=None
        if bars is not None and not bars.empty:
            x=add_indicators(bars) if len(bars)>=20 else bars
            current=float(bars.iloc[-1]["close"])
            if "atr" in x.columns: atr=float(x.iloc[-1]["atr"])
            if len(bars)>=6: five_day=(current/float(bars.iloc[-6]["close"])-1)*100
        if spy_bars is not None and not spy_bars.empty and len(spy_bars)>=6:
            try: market_5d=(float(spy_bars.iloc[-1]["close"])/float(spy_bars.iloc[-6]["close"])-1)*100
            except Exception: market_5d=None
        item=rows[0] if rows else {}
        headline=str(item.get("headline") or item.get("title") or "لا يوجد خبر حديث متاح من المصدر الحالي")
        summary=str(item.get("summary") or "").strip()
        text=f"{headline} {summary}"
        typ=_news_type(text)
        direction,impact=_sentiment(text)
        # Price-risk layer: risk increases if stock already moved strongly in same direction.
        priced=abs(five_day or 0)>=6
        risk=30.0 + (22 if priced else 0)
        if direction=="إيجابي" and (five_day or 0)>4: risk+=10
        if direction=="سلبي" and (five_day or 0)<-4: risk+=10
        if direction=="إيجابي" and market_5d is not None and market_5d < -1.5: risk+=12
        if direction=="سلبي" and market_5d is not None and market_5d > 1.5: risk+=8
        risk=min(90,risk)
        # Impact range is volatility-aware, not a promise.
        if current is not None and atr is not None:
            mult=0.35 + (impact/100)*0.65
            move=atr*mult
            if direction=="إيجابي": up=(current+move*0.55,current+move); down=(current-atr*0.35,current-atr*0.7)
            elif direction=="سلبي": up=(current+atr*0.35,current+atr*0.7); down=(current-move*0.55,current-move)
            else: up=(current+atr*0.35,current+atr*0.6); down=(current-atr*0.35,current-atr*0.6)
        else: up=down=(None,None)
        pub=item.get("created_at") or item.get("updated_at")
        try: pubdt=datetime.fromisoformat(str(pub).replace("Z","+00:00")) if pub else None
        except Exception: pubdt=None
        details=self._details(typ,text)
        headline_ar=await _translate_ar(headline)
        summary_ar=await _translate_ar(summary) if summary else ""
        return {"symbol":symbol,"headline":headline,"summary":summary,"headline_ar":headline_ar,"summary_ar":summary_ar,"type":typ,"direction":direction,"impact":impact,"risk":risk,"priced_in":priced,"five_day":five_day,"market_5d":market_5d,"current":current,"atr":atr,"up":up,"down":down,"published":_time(pubdt.astimezone(timezone.utc)) if pubdt else None,"detected":_time(detected),"details":details,"available":bool(rows)}

    @staticmethod
    def _details(typ,text):
        money=re.findall(r"\$\s?\d+(?:\.\d+)?\s?(?:billion|million|B|M)?",text,re.I)
        pct=re.findall(r"\d+(?:\.\d+)?%",text)
        def one(pattern):
            m=re.search(pattern,text,re.I)
            return m.group(1).strip() if m else None
        return {
            "money":money[:4], "percentages":pct[:4],
            "contract_value": one(r"(?:contract|deal|agreement)[^$]{0,80}(\$\s?\d+(?:\.\d+)?\s?(?:billion|million|B|M)?)") or (money[0] if typ=="عقد / اتفاقية" and money else None),
            "price_target": one(r"price target[^$]{0,30}(\$\s?\d+(?:\.\d+)?)"),
            "dividend": one(r"dividend[^$]{0,40}(\$\s?\d+(?:\.\d+)?)") or (money[0] if typ=="توزيعات أرباح" and money else None),
            "eps": one(r"EPS[^$]{0,30}(\$\s?[-+]?\d+(?:\.\d+)?)"),
            "revenue": one(r"revenue[^$]{0,40}(\$\s?\d+(?:\.\d+)?\s?(?:billion|million|B|M)?)"),
            "note":"أي قيمة تفصيلية تظهر فقط إذا وردت صراحةً في نص الخبر.",
        }

    @staticmethod
    def render_ar(r:dict,sent_at=None)->str:
        sent_at=sent_at or datetime.now(timezone.utc); sent=_time(sent_at)
        emoji="🟢" if r["direction"]=="إيجابي" else "🔴" if r["direction"]=="سلبي" else "🟡"
        headline=html.escape(str(r.get("headline_ar") or r.get("headline") or "")); summary=html.escape(str(r.get("summary_ar") or r.get("summary") or ""))
        lines=[f"📰 <b>أخبار السهم — {r['symbol']}</b>",f"💵 <b>السعر الحالي:</b> {_f(r.get('current'))}",f"🏷️ <b>نوع الخبر:</b> {r['type']}","",f"🗞 <b>الخبر:</b> {headline}"]
        if summary: lines.append(f"📝 <b>الملخص:</b> {summary[:700]}")
        d=r.get("details",{})
        if r["type"]=="عقد / اتفاقية":
            lines += [f"💼 <b>قيمة العقد المذكورة:</b> {d.get('contract_value') or 'غير متاح'}"]
        elif r["type"]=="نتائج أرباح / توجيه":
            lines += [f"💵 <b>EPS المذكور:</b> {d.get('eps') or 'غير متاح'}", f"💰 <b>الإيرادات المذكورة:</b> {d.get('revenue') or 'غير متاح'}"]
        elif r["type"]=="توزيعات أرباح":
            lines += [f"💸 <b>التوزيع المذكور:</b> {d.get('dividend') or 'غير متاح'}"]
        elif r["type"]=="توصية محللين":
            lines += [f"🎯 <b>السعر المستهدف المذكور:</b> {d.get('price_target') or 'غير متاح'}"]
        lines += ["","📊 <b>تقييم الخبر</b>",f"{emoji} <b>التأثير:</b> {r['direction']}",f"🔥 <b>قوة التأثير:</b> {r['impact']:.0f}/100",f"⚠️ <b>عامل الخطر:</b> {r['risk']:.0f}/100",f"💰 <b>هل الخبر مسعّر مسبقًا؟</b> {'محتمل جزئيًا' if r['priced_in'] else 'لا توجد إشارة قوية على ذلك'}"]
        if r.get("five_day") is not None: lines.append(f"📈 <b>حركة السهم آخر 5 جلسات:</b> {r['five_day']:+.2f}%")
        if r.get("market_5d") is not None: lines.append(f"🌐 <b>حركة SPY آخر 5 جلسات:</b> {r['market_5d']:+.2f}%")
        lines += ["","🎯 <b>الأثر السعري المحتمل</b>",f"⬆️ السيناريو الإيجابي: {_f(r['up'][0])} → {_f(r['up'][1])}",f"⬇️ السيناريو السلبي: {_f(r['down'][0])} → {_f(r['down'][1])}",f"🌪 <b>ATR اليومي:</b> {_f(r.get('atr'))}","📌 النطاق تقديري مبني على التذبذب وقوة الخبر، وليس وعدًا بحركة السعر."]
        vals=r.get("details",{}).get("money") or []
        if vals:
            lines += ["","💼 <b>أرقام وردت في الخبر:</b> " + " | ".join(vals)]
        lines += ["","⚠️ <b>تقييم عامل الخطر</b>",f"• تسعير مسبق: {'نعم/جزئي' if r['priced_in'] else 'غير ظاهر بقوة'}","• استمرار التأثير يحتاج تأكيدًا من حركة السعر والحجم والسوق العام."]
        lines += ["","━━━━━━━━━━━━━━","👥 <b>رأي فريق المتداولين الخبراء</b>"]
        direction=r.get("direction")
        impact=float(r.get("impact") or 0); risk=float(r.get("risk") or 0)
        confidence=max(35,min(90,impact-(risk*0.28)))
        if direction=="إيجابي":
            lines += [f"📈 <b>السيناريو المرجح:</b> إيجابي بحذر — ثقة {confidence:.0f}%",
                      f"⏱ <b>أول 30 دقيقة:</b> نراقب الحفاظ فوق {_f(r.get('current'))} وردة فعل الحجم؛ الثبات يدعم التوجه نحو {_f(r['up'][0])}.",
                      f"🕛 <b>منتصف الجلسة:</b> إذا استمر السعر فوق الدعم اللحظي والحجم داعم، يمتد الهدف نحو {_f(r['up'][1])}.",
                      f"❌ <b>إلغاء السيناريو الإيجابي:</b> ضعف السعر مع حجم بيعي واضح باتجاه {_f(r['down'][0])}."]
        elif direction=="سلبي":
            lines += [f"📉 <b>السيناريو المرجح:</b> سلبي بحذر — ثقة {confidence:.0f}%",
                      f"⏱ <b>أول 30 دقيقة:</b> نراقب ضغط البيع؛ استمرار الضعف يدعم التوجه نحو {_f(r['down'][0])}.",
                      f"🕛 <b>منتصف الجلسة:</b> إذا بقي الارتداد ضعيفًا والحجم سلبيًا، يمتد الهبوط نحو {_f(r['down'][1])}.",
                      f"❌ <b>إلغاء السيناريو السلبي:</b> استعادة الزخم والثبات باتجاه {_f(r['up'][0])} مع حجم داعم."]
        else:
            lines += [f"⚖️ <b>السيناريو المرجح:</b> محايد حتى يظهر تأكيد سعري — ثقة {confidence:.0f}%",
                      f"⏱ <b>أول 30 دقيقة:</b> لا نطارد الخبر؛ نراقب الاختراق باتجاه {_f(r['up'][0])} أو الكسر باتجاه {_f(r['down'][0])}.",
                      "🕛 <b>منتصف الجلسة:</b> الاتجاه الذي يحافظ على الحجم والثبات السعري يصبح السيناريو الأقوى."]
        lines += ["📌 الرأي مبني على الخبر + ATR + حركة السهم والسوق، وليس توقّعًا مضمونًا لوقت أو سعر محدد."]
        if r.get("published"):
            lines += ["","🗞 <b>وقت نشر الخبر</b>",f"🗽 نيويورك: {r['published']['ny']}",f"🇸🇦 الرياض: {r['published']['riyadh']}"]
        lines += ["","🕒 <b>وقت اكتشاف التحليل</b>",f"🗽 نيويورك: {r['detected']['ny']}",f"🇸🇦 الرياض: {r['detected']['riyadh']}","📨 <b>وقت إرسال الرسالة</b>",f"🗽 نيويورك: {sent['ny']}",f"🇸🇦 الرياض: {sent['riyadh']}"]
        return "\n".join(("\u200f" + line) if line else "" for line in lines)
