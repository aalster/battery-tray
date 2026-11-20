"""Renders the tray icon: just the number, as large as it fits."""
import winreg

from PIL import Image, ImageDraw, ImageFont

SIZE = 64
MAX_STRETCH = 1.6
LOW_COLOR = "#FF5A5A"
CHARGING_COLOR = "#6EE06E"
STALE_COLOR = "#8C8C8C"


def _font(size: int):
    for name in ("segoeuib.ttf", "arialbd.ttf"):
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


def render(text: str, color: str) -> Image.Image:
    font = _font(96)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font)
    glyphs = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
    ImageDraw.Draw(glyphs).text((-left, -top), text, font=font, fill=color)
    # Fill the full width; stretch vertically (within reason) so digits stay readable at 16px.
    scale = SIZE / glyphs.width
    height = min(SIZE, round(glyphs.height * scale * MAX_STRETCH))
    width = SIZE if height >= glyphs.height * scale else round(glyphs.width * height / glyphs.height)
    glyphs = glyphs.resize((width, height), Image.LANCZOS)
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    img.paste(glyphs, ((SIZE - width) // 2, (SIZE - height) // 2))
    return img
