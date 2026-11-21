"""Renders the tray icon: the device glyph with a vertical battery over its right side."""
import winreg

from PIL import Image, ImageDraw, ImageFont

SIZE = 64  # one tray pixel at 100% scale is 4 units
LOW_COLOR = "#FF5A5A"
WARN_COLOR = "#FFA033"
CHARGING_COLOR = "#5FD35F"

BATTERY_LEFT = 36    # the battery covers the lower right part of the glyph
BATTERY_TOP = 16     # the cap sits above it
LINE = 4


def _glyph_font(size: int):
    for name in ("SegoeIcons.ttf", "segmdl2.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def theme_color() -> str:
    """White on a dark taskbar, black on a light one."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            light = winreg.QueryValueEx(k, "SystemUsesLightTheme")[0]
    except OSError:
        light = 0
    return "#000000" if light else "#FFFFFF"


_BOLT = ((37, 20), (22, 39), (31, 39), (28, 52), (43, 33), (34, 33))


def app_icon(color: str, size: int = SIZE, back: str | None = None) -> Image.Image:
    """The application's own icon: a solid upright battery with a lightning bolt cut out of it.

    `back` puts it on a rounded tile, for places where the background is unknown (the exe file).
    """
    ss = 8  # drawn large and scaled down: Pillow does not antialias shapes
    img = Image.new("RGBA", (64 * ss, 64 * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def box(*xy):
        return tuple(v * ss for v in xy)

    draw.rounded_rectangle(box(13, 9, 51, 62), 9 * ss, fill=color)
    draw.rounded_rectangle(box(24, 2, 40, 7), 2 * ss, fill=color)
    draw.polygon([(x * ss, y * ss) for x, y in _BOLT], fill=(0, 0, 0, 0))
    if back:
        tile = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(tile).rounded_rectangle(box(0, 0, 64, 64), 14 * ss, fill=back)
        inner = img.resize((44 * ss, 44 * ss), Image.LANCZOS)
        tile.alpha_composite(inner, (10 * ss, 10 * ss))
        img = tile
    return img.resize((size, size), Image.LANCZOS)


def render(glyph: str, percent: int | None, fill: str, outline: str) -> Image.Image:
    """`fill` colours the charge level inside the battery; `outline` draws the glyph and the battery body."""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # The stroke thickens the hairline glyphs, which otherwise fade away at 16px.
    draw.text((28, 30), glyph, font=_glyph_font(58), fill=outline, anchor="mm", stroke_width=1, stroke_fill=outline)

    left, right, bottom, cap = BATTERY_LEFT, SIZE - 1, SIZE - 1, BATTERY_TOP - 6
    # Clear a thin gap around the battery so it reads as lying on top of the glyph.
    draw.rectangle((left - 3, BATTERY_TOP - 3, right, bottom), fill=(0, 0, 0, 0))
    draw.rectangle((left + 5, cap - 3, right - 5, BATTERY_TOP), fill=(0, 0, 0, 0))
    draw.rectangle((left + 8, cap, right - 8, BATTERY_TOP), fill=outline)  # cap
    draw.rectangle((left, BATTERY_TOP, right, bottom), outline=outline, width=LINE)
    if percent:
        inner = bottom - LINE - (BATTERY_TOP + LINE) + 1
        level = max(LINE, round(inner * min(percent, 100) / 100))
        draw.rectangle((left + LINE, bottom - LINE - level + 1, right - LINE, bottom - LINE), fill=fill)
    return img
