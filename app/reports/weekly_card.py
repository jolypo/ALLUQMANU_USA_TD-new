from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.config import settings

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except Exception:  # pragma: no cover
    arabic_reshaper = None
    get_display = None

W, H = 1600, 1400
BG = (3, 4, 6)
PANEL = (27, 28, 31)
PANEL_2 = (34, 34, 36)
BORDER = (154, 131, 101)
TEXT = (240, 238, 235)
MUTED = (180, 176, 169)
GREEN = (92, 181, 105)
RED = (218, 68, 69)
BEIGE = (196, 166, 126)


def _font(size: int, bold: bool = False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _ar(value) -> str:
    text = str(value)
    if arabic_reshaper is not None and get_display is not None:
        try:
            return get_display(arabic_reshaper.reshape(text))
        except Exception:
            pass
    return text


def _rtl(draw: ImageDraw.ImageDraw, xy, text, font, fill=TEXT, anchor="ra"):
    value = str(text)
    if arabic_reshaper is not None and get_display is not None:
        draw.text(xy, _ar(value), font=font, fill=fill, anchor=anchor)
        return
    try:
        draw.text(xy, value, font=font, fill=fill, anchor=anchor, direction="rtl", language="ar")
    except Exception:
        draw.text(xy, value, font=font, fill=fill, anchor=anchor)


def _txt(draw: ImageDraw.ImageDraw, xy, text, font, fill=TEXT, anchor="la"):
    draw.text(xy, str(text), font=font, fill=fill, anchor=anchor)


def _round(draw, box, radius=26, fill=PANEL, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _fmt_money(v: float) -> str:
    return f"${float(v):,.2f}"


def _fmt_price(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "N/A"
    if abs(f) >= 1000:
        return f"{f:,.0f}"
    return f"{f:,.2f}"


def _period_title(report: dict) -> tuple[str, str, str]:
    category = report.get("category", "index_option")
    period = report.get("period", "daily")
    title_map = {
        "stock": "تقرير الأسهم",
        "equity_option": "تقرير عقود الأسهم",
        "index_option": "تقرير SPX",
        "options_all": "تقرير جميع العقود",
    }
    suffix = "اليومي" if period == "daily" else "الأسبوعي"
    watermark = {
        "stock": "STOCKS",
        "equity_option": "OPTIONS",
        "index_option": "SPX",
        "options_all": "ALL OPTIONS",
    }.get(category, "REPORT")
    horizon = str(report.get("horizon") or "all").lower()
    horizon_suffix = {
        "daily": " — 0DTE",
        "weekly": " — 1–7 DTE",
        "monthly": " — 8–35 DTE",
    }.get(horizon, "")
    return f"{title_map.get(category, 'تقرير الأداء')} {suffix}{horizon_suffix}", suffix, watermark


def _arabic_date_label(report: dict) -> str:
    raw = report.get("report_date_ny")
    try:
        current = datetime.fromisoformat(str(raw)).date()
    except Exception:
        current = datetime.utcnow().date()

    days = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    months = [
        "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
        "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
    ]
    if report.get("period") == "daily":
        return f"{days[current.weekday()]} {current.day} {months[current.month - 1]} {current.year}"

    try:
        start = datetime.fromisoformat(str(report.get("period_start")).replace("Z", "+00:00")).date()
        if start.month == current.month:
            return f"{start.day}–{current.day} {months[current.month - 1]} {current.year}"
        return f"{start.day} {months[start.month - 1]} – {current.day} {months[current.month - 1]} {current.year}"
    except Exception:
        return f"الأسبوع المنتهي {current.day} {months[current.month - 1]} {current.year}"


def _result_label(row: dict) -> tuple[str, tuple[int, int, int]]:
    result = str(row.get("result", "OPEN")).upper()
    if result == "WIN":
        return "WIN", GREEN
    if result == "LOSS":
        return "LOSS", RED
    if result == "BREAKEVEN":
        return "BE", MUTED
    if row.get("success"):
        return "SUCCESS / OPEN", GREEN
    return "OPEN", MUTED


def _success_note_lines(report: dict) -> list[str]:
    rule = report.get("success_rule") or {}
    threshold = float(rule.get("threshold", 0) or 0)
    category = report.get("category")
    if category == "stock":
        return [
            "الأسهم: الصفقة ناجحة عند تحقق أحد الأهداف TP1 / TP2 / TP3.",
            "الصفقة المغلقة بدون تحقق أي هدف تسجل خاسرة في تقييم الأداء.",
        ]
    if category == "options_all":
        return [
            "التقرير الشامل يجمع كل العقود التي تم الدخول فيها خلال نفس الفترة فقط.",
            "معيار النجاح لكل فئة يُطبق حسب إعداد عقود الأسهم أو عقود SPX الخاص بها.",
        ]
    if threshold <= 0:
        return [
            "معيار نجاح العقود معطل حاليًا.",
            "لن تُحسم نتيجة أداء العقود حتى يتم تحديد حد نجاح أكبر من صفر.",
        ]
    return [
        f"العقد الذي يحقق {threshold:,.2f} دولار فأكثر يسجل صفقة ناجحة فورًا.",
        "إذا انتهت جلسة نيويورك دون بلوغ الحد يسجل العقد صفقة خاسرة في الأداء.",
    ]


def performance_report_card(report: dict, output_path: str):
    summary = report.get("summary") or {}
    financial = report.get("financial") or {}
    rows = list(report.get("display_rows") or [])[:8]
    category = report.get("category", "index_option")
    title, period_suffix, big_mark = _period_title(report)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Header
    _rtl(d, (70, 55), title, _font(46, True), BEIGE, anchor="la")
    _txt(d, (1530, 55), settings.watermark_name, _font(42, True), BEIGE, anchor="ra")
    _txt(d, (1535, 120), big_mark, _font(102, True), (22, 23, 25), anchor="ra")

    _round(d, (40, 112, 540, 168), radius=22, fill=(8, 9, 11), outline=(135, 132, 127), width=2)
    _rtl(d, (510, 142), _arabic_date_label(report), _font(26, True), TEXT)
    _rtl(d, (88, 142), "يومي" if report.get("period") == "daily" else "أسبوعي", _font(22, True), MUTED, anchor="la")

    # Top statistic cards
    card_y1, card_y2 = 215, 430
    gap = 18
    card_w = (W - 80 - gap * 3) // 4
    if category == "stock":
        success_sub = "تحقق هدف واحد على الأقل"
        loss_sub = "أغلقت بدون تحقق هدف"
    else:
        success_sub = "وصلت لحد الربح المحدد"
        loss_sub = "لم تصل للحد حتى إغلاق السوق"
    stats = [
        ("إجمالي الصفقات", summary.get("activity", 0), f"المحسومة: {summary.get('trades',0)} | انتظار: {summary.get('pending',0)}", BEIGE),
        ("الصفقات الناجحة", summary.get("wins", 0), success_sub, GREEN),
        ("الصفقات الخاسرة", summary.get("losses", 0), loss_sub, RED),
        ("نسبة النجاح", f"{float(summary.get('win_rate', 0)):.2f}%", f"W/L: {summary.get('wins',0)}/{summary.get('losses',0)}", GREEN),
    ]
    for i, (label, value, sub, accent) in enumerate(stats):
        x1 = 40 + i * (card_w + gap)
        x2 = x1 + card_w
        _round(d, (x1, card_y1, x2, card_y2), radius=34, fill=PANEL, outline=(48, 49, 52), width=2)
        _rtl(d, (x2 - 28, card_y1 + 38), label, _font(27, True), TEXT)
        _txt(d, (x1 + card_w / 2, card_y1 + 118), value, _font(52, True), accent, anchor="mm")
        _rtl(d, (x2 - 28, card_y2 - 34), sub, _font(18), MUTED)

    # Dynamic success-rule note
    _round(d, (40, 455, W - 40, 540), radius=18, fill=(9, 10, 12), outline=(58, 58, 59), width=1)
    note_lines = _success_note_lines(report)
    _rtl(d, (W - 65, 482), "ملاحظة: " + note_lines[0], _font(18, True), MUTED)
    _rtl(d, (W - 65, 515), note_lines[1], _font(18, True), MUTED)

    # Table header
    table_top = 565
    cols = [
        (40, 95, "#"),
        (95, 390, "الأصل / العقد"),
        (390, 565, "النوع"),
        (565, 765, "سعر الدخول"),
        (765, 970, "أعلى سعر"),
        (970, 1190, "أفضل ربح"),
        (1190, 1560, "النتيجة النهائية / الحالة"),
    ]
    for x1, x2, label in cols:
        if label == "#":
            _txt(d, ((x1+x2)//2, table_top), label, _font(22, True), MUTED, anchor="ma")
        else:
            _rtl(d, (x2 - 10, table_top), label, _font(22, True), MUTED)

    row_h = 72
    y = table_top + 45
    if not rows:
        _round(d, (40, y, W - 40, y + 90), radius=18, fill=PANEL_2, outline=BORDER, width=1)
        _rtl(d, (W - 70, y + 52), "لا توجد صفقات مسجلة لهذه الفترة.", _font(26, True), MUTED)
        y += 105
    else:
        for idx, row in enumerate(rows, start=1):
            _round(d, (40, y, W - 40, y + 58), radius=15, fill=PANEL_2, outline=BORDER, width=1)
            _txt(d, (68, y + 30), idx, _font(22, True), TEXT, anchor="mm")
            _txt(d, (365, y + 30), row.get("contract", "N/A"), _font(22, True), TEXT, anchor="ra")
            kind = str(row.get("kind", "N/A"))
            kind_color = GREEN if kind in {"CALL", "LONG"} else RED if kind in {"PUT", "SHORT"} else TEXT
            _txt(d, (540, y + 30), kind, _font(21, True), kind_color, anchor="ra")
            _txt(d, (740, y + 30), _fmt_price(row.get("entry")), _font(21, True), TEXT, anchor="ra")
            _txt(d, (945, y + 30), _fmt_price(row.get("best_price")), _font(21, True), TEXT, anchor="ra")
            if category == "stock":
                best = f"{float(row.get('best_profit',0)):+.2f}%"
            else:
                best = _fmt_money(row.get("best_profit", 0))
            _txt(d, (1165, y + 30), best, _font(21, True), GREEN if float(row.get("best_profit",0)) > 0 else MUTED, anchor="ra")
            result_text, result_color = _result_label(row)
            _txt(d, (1535, y + 30), result_text, _font(20, True), result_color, anchor="ra")
            y += row_h

    # Bottom financial cards
    bottom_y1 = 1165
    bottom_y2 = 1345
    if category == "stock":
        vals = [
            ("إجمالي العائد الموجب", f"+{float(financial.get('gross_profit',0)):.2f}%", GREEN, "صفقات مغلقة فقط"),
            ("مجموع العائد الخاسر", f"-{float(financial.get('gross_loss',0)):.2f}%", RED, "خسائر محققة فقط"),
            ("صافي العائد", f"{float(financial.get('net',0)):+.2f}%", BEIGE, "Realized P&L"),
        ]
    else:
        vals = [
            ("إجمالي الربح", _fmt_money(financial.get("gross_profit", 0)), GREEN, f"{float(financial.get('gross_profit_sar',0)):,.2f} ريال"),
            ("مجموع الخسارة", _fmt_money(financial.get("gross_loss", 0)), RED, f"{float(financial.get('gross_loss_sar',0)):,.2f} ريال"),
            ("صافي الربح", _fmt_money(financial.get("net", 0)), BEIGE, f"{float(financial.get('net_sar',0)):,.2f} ريال"),
        ]
    bw = (W - 80 - 32) // 3
    for i, (label, value, accent, sub) in enumerate(vals):
        x1 = 40 + i * (bw + 16)
        x2 = x1 + bw
        _round(d, (x1, bottom_y1, x2, bottom_y2), radius=28, fill=PANEL, outline=BORDER, width=2)
        _rtl(d, (x2 - 25, bottom_y1 + 45), label, _font(27, True), TEXT)
        _txt(d, (x1 + bw / 2, bottom_y1 + 104), value, _font(38, True), accent, anchor="mm")
        _txt(d, (x1 + bw / 2, bottom_y2 - 28), sub, _font(19, True), TEXT, anchor="mm")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)


def weekly_performance_card(report: dict, output_path: str):
    """Backward-compatible name used by existing imports."""
    performance_report_card(report, output_path)
