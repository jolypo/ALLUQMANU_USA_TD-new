from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.config import settings


# Profit-card color rules previously approved by the user:
#   < $100       -> green/cyan
#   $100-$299.99 -> yellow/gold
#   >= $300      -> blue
# The card is used only for positive-profit alerts/milestones. Losses keep their
# own reporting path and must not be styled as "مبروك الأرباح".


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _tier(profit_usd: float) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Return (accent, bright) for the approved profit tiers."""
    if profit_usd >= 300:
        return (55, 145, 255), (170, 215, 255)  # blue
    if profit_usd >= 100:
        return (255, 194, 52), (255, 232, 145)  # gold/yellow
    return (42, 235, 186), (150, 255, 226)  # green/cyan


def _rounded_neon_frame(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    radius: int,
    accent: tuple[int, int, int],
) -> None:
    """Draw a layered neon rounded frame similar to the approved reference."""
    x1, y1, x2, y2 = box
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for width, alpha in ((28, 28), (18, 50), (10, 92)):
        gd.rounded_rectangle(
            box,
            radius=radius,
            outline=(*accent, alpha),
            width=width,
        )
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    canvas.alpha_composite(glow)

    sharp = ImageDraw.Draw(canvas)
    sharp.rounded_rectangle(
        box,
        radius=radius,
        outline=(*accent, 255),
        width=5,
    )


def _draw_centered_neon_text(
    canvas: Image.Image,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    accent: tuple[int, int, int],
    bright: tuple[int, int, int],
    *,
    direction: str | None = None,
    stroke_width: int = 2,
) -> int:
    """Draw centered text with a soft neon halo; returns rendered width."""
    kwargs = {"font": font}
    if direction:
        kwargs["direction"] = direction

    probe = ImageDraw.Draw(canvas)
    try:
        bbox = probe.textbbox((0, 0), text, **kwargs)
    except TypeError:
        kwargs.pop("direction", None)
        bbox = probe.textbbox((0, 0), text, **kwargs)
    width = bbox[2] - bbox[0]
    x = (canvas.width - width) // 2

    # Neon halo layers.
    for blur_radius, alpha, sw in ((20, 85, stroke_width + 7), (10, 140, stroke_width + 4), (4, 205, stroke_width + 2)):
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        draw_kwargs = dict(kwargs)
        ld.text(
            (x, y),
            text,
            fill=(*accent, alpha),
            stroke_width=sw,
            stroke_fill=(*accent, alpha),
            **draw_kwargs,
        )
        layer = layer.filter(ImageFilter.GaussianBlur(blur_radius))
        canvas.alpha_composite(layer)

    draw = ImageDraw.Draw(canvas)
    draw_kwargs = dict(kwargs)
    draw.text(
        (x, y),
        text,
        fill=(*accent, 245),
        stroke_width=stroke_width,
        stroke_fill=(*bright, 255),
        **draw_kwargs,
    )
    return width


def profit_update_card(
    trade: dict,
    profit_usd: float,
    profit_sar: float,
    current_price: float,
    output_path: str,
):
    """
    Render the approved minimalist "مبروك الأرباح" neon card.

    Only the actual USD profit is shown in the artwork so the image remains
    visually identical to the approved reference. Detailed trade/contract/SAR
    data stays in the Telegram caption. The amount is always dynamic.
    """
    # This card is intentionally a profit-only visual. Defensive clamp prevents
    # a negative number from ever being displayed as a congratulatory card if a
    # caller regresses in the future.
    display_profit = max(0.0, float(profit_usd or 0.0))
    accent, bright = _tier(display_profit)

    width, height = 1200, 900
    base = Image.new("RGBA", (width, height), (5, 8, 10, 255))

    # Subtle vertical texture / dark vignette similar to the approved image.
    texture = Image.new("RGBA", base.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(texture)
    for x in range(0, width, 4):
        a = 10 + ((x * 17) % 15)
        td.line((x, 0, x, height), fill=(85, 90, 92, a), width=1)
    texture = texture.filter(ImageFilter.GaussianBlur(0.7))
    base.alpha_composite(texture)

    vignette = Image.new("L", base.size, 0)
    vd = ImageDraw.Draw(vignette)
    vd.ellipse((-260, -180, width + 260, height + 260), fill=175)
    vignette = vignette.filter(ImageFilter.GaussianBlur(130))
    dark = Image.new("RGBA", base.size, (0, 0, 0, 155))
    dark.putalpha(Image.eval(vignette, lambda p: 255 - p))
    base.alpha_composite(dark)

    _rounded_neon_frame(base, (38, 35, width - 38, height - 35), 34, accent)

    title_font = _font(72, True)
    amount_font = _font(185, True)
    watermark_font = _font(27, True)

    _draw_centered_neon_text(
        base,
        165,
        "مبروك الأرباح",
        title_font,
        accent,
        bright,
        direction="rtl",
        stroke_width=1,
    )

    # Keep whole-dollar amounts clean (e.g. + 50 $) while retaining cents when
    # the real P&L contains them.
    if abs(display_profit - round(display_profit)) < 0.005:
        amount = f"+ {display_profit:,.0f} $"
    else:
        amount = f"+ {display_profit:,.2f} $"

    _draw_centered_neon_text(
        base,
        390,
        amount,
        amount_font,
        accent,
        bright,
        stroke_width=2,
    )

    # Very subtle brand mark; intentionally does not compete with the approved
    # minimalist layout.
    watermark = str(getattr(settings, "watermark_name", "ALLUQMANU_USA_TD") or "ALLUQMANU_USA_TD")
    d = ImageDraw.Draw(base)
    wb = d.textbbox((0, 0), watermark, font=watermark_font)
    ww = wb[2] - wb[0]
    d.text(
        ((width - ww) // 2, height - 95),
        watermark,
        font=watermark_font,
        fill=(*accent, 65),
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(output_path, "PNG", optimize=True)
