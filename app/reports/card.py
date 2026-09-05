from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from app.config import settings

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except Exception:  # pragma: no cover - Pillow/Render fallback
    arabic_reshaper = None
    get_display = None


W, H = 2048, 680
NY = ZoneInfo("America/New_York")


def _font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _ar(text: str) -> str:
    text = str(text)
    if arabic_reshaper is not None and get_display is not None:
        try:
            return get_display(arabic_reshaper.reshape(text))
        except Exception:
            pass
    return text


def _date_text(signal: dict) -> str:
    raw = signal.get("published_at") or signal.get("created_at")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(NY).date().isoformat()
        except Exception:
            pass
    return datetime.now(NY).date().isoformat()


def _option_subtitle(signal: dict) -> str:
    option = signal.get("option") or {}
    exp = option.get("expiration")
    dte = option.get("dte")
    if exp and dte not in (None, ""):
        return f"EXP {exp} • DTE {dte}"
    if exp:
        return f"EXP {exp}"
    return _date_text(signal)


def _fmt_number(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value or "N/A")
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return f"{f:.2f}".rstrip("0").rstrip(".")


def _right_text(draw, xy, text, font, fill):
    x, y = xy
    value = str(text)
    if arabic_reshaper is not None and get_display is not None:
        draw.text((x, y), _ar(value), font=font, fill=fill, anchor="ra")
        return
    try:
        draw.text((x, y), value, font=font, fill=fill, anchor="ra", direction="rtl", language="ar")
    except Exception:
        draw.text((x, y), value, font=font, fill=fill, anchor="ra")


def _center_text(draw, xy, text, font, fill):
    draw.text(xy, str(text), font=font, fill=fill, anchor="mm")


def _make_background(accent: tuple[int, int, int]) -> Image.Image:
    # Near-black base with subtle blue/navy center.
    img = Image.new("RGB", (W, H), (3, 7, 13))
    px = img.load()
    ar, ag, ab = accent
    for y in range(H):
        for x in range(W):
            edge = min(x, W - 1 - x, y, H - 1 - y)
            edge_strength = max(0.0, 1.0 - edge / 180.0)
            center = 1.0 - min(1.0, abs(x - W / 2) / (W / 2))
            base_b = int(11 + 5 * center)
            glow = edge_strength * 0.09
            px[x, y] = (
                min(255, int(3 + ar * glow)),
                min(255, int(7 + ag * glow)),
                min(255, int(base_b + ab * glow)),
            )

    # Soft accent flares at lower corners to resemble the supplied model.
    glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    for width, alpha in ((38, 28), (22, 45), (9, 85), (3, 180)):
        gd.arc((-220, 255, 850, 990), 175, 294, fill=(*accent, alpha), width=width)
        gd.arc((W - 860, 210, W + 245, 995), 242, 355, fill=(*accent, alpha), width=width)
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(5))
    return Image.alpha_composite(img.convert("RGBA"), glow_layer)


def option_card(signal: dict, path: str):
    """Generate the requested horizontal CALL/PUT entry card.

    The detailed Telegram signal text remains unchanged; this function only
    changes the visual card. CALL is green, PUT is red.
    """
    option = signal.get("option") or {}
    option_type = str(option.get("type") or "CALL").upper()
    is_call = option_type == "CALL"
    accent = (72, 222, 102) if is_call else (255, 45, 52)
    muted = (145, 153, 164)
    white = (248, 248, 248)

    img = _make_background(accent)
    draw = ImageDraw.Draw(img, "RGBA")

    # Outer rounded neon border.
    draw.rounded_rectangle(
        (24, 22, W - 24, H - 26),
        radius=64,
        outline=(*accent, 225),
        width=3,
    )

    # Vertical separators.
    sep1, sep2 = 720, 1355
    for x in (sep1, sep2):
        draw.line((x, 125, x, 482), fill=(155, 165, 175, 140), width=2)

    # Header icons and Arabic labels.
    hdr_font = _font(42, True)
    icon_font = _font(34, True)
    for cx, label, icon in (
        (254, "الشركة", "⊙"),
        (900, "سعر التنفيذ", "⊙"),
        (1547, "سعر الدخول", "$"),
    ):
        draw.ellipse((cx - 22, 109, cx + 22, 153), outline=(*accent, 245), width=3)
        _center_text(draw, (cx, 131), icon, icon_font, accent)
        _right_text(draw, (cx + 192, 154), label, hdr_font, muted)

    # Company / symbol + actual option expiration subtitle.
    symbol = str(signal.get("symbol") or "N/A").upper()
    symbol_font_size = 150 if len(symbol) <= 5 else 118
    _center_text(draw, (370, 302), symbol, _font(symbol_font_size, True), white)
    subtitle = _option_subtitle(signal)
    subtitle_font = _font(44 if len(subtitle) > 20 else 52, False)
    _center_text(draw, (370, 451), subtitle, subtitle_font, muted)

    # Strike and option type.
    strike = _fmt_number(option.get("strike", "N/A"))
    _center_text(draw, (1037, 295), strike, _font(152, True), white)
    pill = (860, 408, 1178, 500)
    draw.rounded_rectangle(pill, radius=25, outline=(*accent, 255), width=4)
    _center_text(draw, ((pill[0] + pill[2]) // 2, (pill[1] + pill[3]) // 2 + 2), option_type, _font(58, True), accent)

    # Single displayed entry price = conservative buy edge (Ask/entry_high).
    entry = option.get("entry_high", signal.get("entry_high"))
    _center_text(draw, (1698, 300), _fmt_number(entry), _font(154, True), white)

    # Long, understated watermark across the lower center.
    watermark = str(settings.watermark_name or "ALLUQMANI_USA_TD")
    wm_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    wd = ImageDraw.Draw(wm_layer)
    wm_font = _font(100, True)
    wd.text((W // 2, 575), watermark, font=wm_font, fill=(125, 130, 138, 24), anchor="mm")
    img = Image.alpha_composite(img, wm_layer)

    img.convert("RGB").save(path, "PNG", optimize=True)
