"""Settings window: device cards that open per-device pages, drawn on one canvas.

Tk lives in the main thread; other threads talk to it through app.ui_queue.
"""
import ctypes
import io
import logging
import queue
import tkinter as tk
import winreg
from tkinter import font as tkfont

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageTk

from . import autostart, devtype, icon

log = logging.getLogger(__name__)
WIDTH, HEIGHT, MARGIN = 480, 640, 20
ROOT_TITLE = "Пристрої"
LINGER_CHOICES = (0, 1, 5, 10)
POLL_CHOICES = (15, 30, 60, 120)
SHOW_TITLE = "Показувати іконку в треї, коли батарея"
COLOR_TITLE = "Колір батареї"
# Settings a device can override: (name, title, choices, how a value is written)
OPTIONS = (
    ("linger_minutes", "Показувати після відключення ще", LINGER_CHOICES, "{} хв".format),
    ("show_offline", "Показувати, коли пристрій не підключено", (False, True), lambda v: "Так" if v else "Ні"),
)

LIGHT = dict(bg="#F3F3F3", card="#FFFFFF", border="#E0E0E0", hover="#ABABAB", text="#1B1B1B", muted="#5F5F5F", dim="#9B9B9B",
             accent="#0067C0", on_accent="#FFFFFF", tile="#EAF1F8", low="#C42B1C", warn="#B25E00", good="#0F7B0F", shadow=46)
DARK = dict(bg="#202020", card="#2B2B2B", border="#3D3D3D", hover="#646464", text="#FFFFFF", muted="#C8C8C8", dim="#7D7D7D",
            accent="#60CDFF", on_accent="#000000", tile="#3A3A3A", low="#FF99A4", warn="#FFB454", good="#6CCB5F", shadow=140)


def _options(choices, current, fmt) -> list:
    """Pills for the choices, plus the current value when it is not one of them (set by hand in the file)."""
    values = list(choices) if current is None or current in choices else sorted({*choices, current})
    return [(v, fmt(v)) for v in values]


def _dark_mode() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return not winreg.QueryValueEx(k, "AppsUseLightTheme")[0]
    except OSError:
        return False


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.page = ("home", None)
        self.snap = []            # last app.snapshot()
        self.visible = False
        self.tick_id = None
        self.dark = None
        self.colors = LIGHT
        self.images = {}          # shape parameters -> (PhotoImage, padding)
        self.fonts = {}
        self.hits = []            # clickable areas: (x0, y0, x1, y1, on_click, on_hover)
        self.hover = None
        self.widgets = []         # real widgets embedded in the canvas
        self.before_leave = None  # commits the name being edited
        self.status_item = None

        self.root = root = tk.Tk()
        root.withdraw()
        root.title("Battery Tray")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.hide)
        root.report_callback_exception = lambda *exc: log.error("window callback failed", exc_info=exc)
        self.scale = root.winfo_fpixels("1i") / 96
        self.icon_font = "Segoe Fluent Icons" if "Segoe Fluent Icons" in tkfont.families() else "Segoe MDL2 Assets"
        self.icon_colors = None
        self.drag = None          # the slider handle being dragged: called with the pointer's x
        self.at = (0, 0)          # the pointer, in canvas coordinates

        self.canvas = c = tk.Canvas(root, width=self.px(WIDTH), height=self.px(HEIGHT), highlightthickness=0, bd=0)
        c.pack()
        c.bind("<Motion>", lambda e: self._point(e.x, e.y))
        c.bind("<Leave>", lambda e: self._set_hover(None))
        c.bind("<Button-1>", self._click)
        c.bind("<B1-Motion>", lambda e: self.drag(c.canvasx(e.x)) if self.drag else self._point(e.x, e.y))
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<MouseWheel>", lambda e: self._scroll(-e.delta // 120))
        root.bind("<Escape>", lambda e: self.go("home") if self.page[0] != "home" else self.hide())

        root.after(200, self._pump)

    def run(self):
        self.root.mainloop()

    def _pump(self):
        try:
            while True:
                command = self.app.ui_queue.get_nowait()
                if command == "quit":
                    self.root.destroy()
                    return
                if command == "settings":
                    self.show()
                if command == "update" and self.visible:
                    self._update()
        except queue.Empty:
            pass
        self.root.after(200, self._pump)

    def show(self):
        if not self.visible:
            self._apply_theme()
            self.snap = self.app.snapshot()
            self.go("home")
            self.visible = True
            self.tick_id = self.root.after(1000, self._tick)
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        self._leave()
        if self.tick_id is not None:
            self.root.after_cancel(self.tick_id)
            self.tick_id = None
        self.visible = False
        self.root.withdraw()

    def _apply_theme(self):
        theme = self.app.settings["theme"]
        dark = theme == "dark" if theme in ("light", "dark") else _dark_mode()
        self._set_icons(icon.theme_color(), "#FFFFFF" if dark else "#000000")
        if dark == self.dark:
            return
        self.dark, self.colors = dark, DARK if dark else LIGHT
        self.images.clear()
        self.root.update_idletasks()
        try:  # DWMWA_USE_IMMERSIVE_DARK_MODE: title bar follows the theme
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(dark)), 4)
        except (OSError, AttributeError):
            pass

    def _set_icons(self, big: str, small: str):
        """The big icon is on the taskbar, which follows the system theme; the small one is in the title bar."""
        if (big, small) == self.icon_colors:
            return
        self.icon_colors = (big, small)
        user32 = ctypes.windll.user32
        user32.CreateIconFromResourceEx.restype = user32.GetParent.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        self.root.update_idletasks()
        hwnd = user32.GetParent(self.root.winfo_id())
        for which, metric, color in ((1, 11, big), (0, 49, small)):  # ICON_BIG / SM_CXICON, ICON_SMALL / SM_CXSMICON
            size = user32.GetSystemMetrics(metric)
            png = io.BytesIO()
            icon.app_icon(color, size).save(png, "PNG")
            handle = user32.CreateIconFromResourceEx(png.getvalue(), len(png.getvalue()), True, 0x00030000, size, size, 0)
            user32.SendMessageW(hwnd, 0x0080, which, handle)  # WM_SETICON

    def _tick(self):
        self._update()
        self.tick_id = self.root.after(1000, self._tick)

    def _update(self):
        """Redraws the page if the devices changed."""
        snap = self.app.snapshot()
        if snap != self.snap and not self.drag:
            self.snap = snap
            if self.page[0] == "device":  # do not rebuild the page under the user's cursor
                d = self._find(self.page[1])
                if d:
                    self.canvas.itemconfigure(self.status_item, text=self._status(d, percent=True))
            else:
                self.render()

    # --- navigation ---

    def go(self, kind: str, arg=None):
        self._leave()
        self.page = (kind, arg)
        self.canvas.yview_moveto(0)
        self.render()

    def changed(self):
        """A setting was changed on the current page."""
        self.snap = self.app.snapshot()
        self.render()

    def _leave(self):
        if self.before_leave:
            commit, self.before_leave = self.before_leave, None
            commit()

    def render(self):
        self._leave()
        c = self.canvas
        for widget in self.widgets:
            widget.destroy()
        self.widgets.clear()
        self.hits.clear()
        self.hover = None
        c.delete("all")
        c.config(bg=self.colors["bg"], cursor="")
        kind, arg = self.page
        bottom = {"home": self._home, "device": self._device, "settings": self._settings}[kind](arg)
        c.config(scrollregion=(0, 0, self.px(WIDTH), max(bottom + self.px(MARGIN), self.px(HEIGHT))))
        self._scroll()
        self._point(c.winfo_pointerx() - c.winfo_rootx(), c.winfo_pointery() - c.winfo_rooty())

    def _scroll(self, units: int = 0):
        """Scrolls the page and redraws the thin scroll indicator (there is one only when the page does not fit)."""
        c = self.canvas
        c.yview_scroll(units, "units")
        c.delete("thumb")
        c.update_idletasks()  # the scroll region applies on idle
        first, last = c.yview()
        if last - first < 1:
            view = c.winfo_height()
            top = c.canvasy(0) + first * view
            c.create_rectangle(self.px(WIDTH - 5), top + self.px(2), self.px(WIDTH - 2), top + (last - first) * view - self.px(2),
                               fill=self.colors["dim"], width=0, tags="thumb")

    # --- pointer ---

    def _point(self, x: int, y: int):
        x, y = self.at = self.canvas.canvasx(x), self.canvas.canvasy(y)
        self._set_hover(next((h for h in self.hits if h[0] <= x < h[2] and h[1] <= y < h[3]), None))

    def _set_hover(self, hit):
        if hit is self.hover:
            return
        if self.hover and self.hover[5]:
            self.hover[5](False)
        self.hover = hit
        if hit and hit[5]:
            hit[5](True)
        self.canvas.config(cursor="hand2" if hit else "")

    def _click(self, event):
        self.canvas.focus_set()  # takes focus from the name field, which commits it
        self._point(event.x, event.y)
        if self.hover:
            self.hover[4]()

    def _release(self, event):
        if self.drag:
            drag, self.drag = self.drag, None
            drag(self.canvas.canvasx(event.x), done=True)

    def hit(self, x0, y0, x1, y1, on_click, on_hover=None):
        self.hits.append((x0, y0, x1, y1, on_click, on_hover))

    # --- drawing ---

    def px(self, n: float) -> int:
        return round(n * self.scale)

    def font(self, size: int, bold: bool = False):
        return ("Segoe UI Semibold" if bold else "Segoe UI", -self.px(size))

    def fit(self, text: str, font, width: int) -> str:
        """Cuts the text with an ellipsis so that it fits the width."""
        f = self.fonts.get(font) or self.fonts.setdefault(font, tkfont.Font(font=font))
        if f.measure(text) <= width:
            return text
        while text and f.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def measure(self, text: str, font) -> int:
        f = self.fonts.get(font) or self.fonts.setdefault(font, tkfont.Font(font=font))
        return f.measure(text)

    def text(self, x, y, text, size=13, bold=False, color="text", anchor="nw"):
        return self.canvas.create_text(x, y, text=text, font=self.font(size, bold), fill=self.colors[color], anchor=anchor)

    def glyph(self, x, y, char, size, color="text"):
        return self.canvas.create_text(x, y, text=char, font=(self.icon_font, -self.px(size)), fill=self.colors[color])

    def _shape(self, w, h, radius, fill, border, width=1, shadow=0):
        """A rounded rectangle; `shadow` is the opacity (0-255) of a soft drop shadow under it."""
        key = (w, h, radius, fill, border, width, shadow)
        if key not in self.images:
            ss = 3  # supersampling: Pillow does not antialias shapes
            pad = self.px(16) if shadow else 0
            size = ((w + 2 * pad) * ss, (h + 2 * pad) * ss)
            box = (pad * ss, pad * ss, (pad + w) * ss - 1, (pad + h) * ss - 1)
            if shadow:
                drop = self.px(4) * ss
                mask = Image.new("L", size, 0)
                ImageDraw.Draw(mask).rounded_rectangle((box[0], box[1] + drop, box[2], box[3] + drop), radius * ss, fill=shadow)
                img = Image.new("RGBA", size, (0, 0, 0, 0))
                img.putalpha(mask.filter(ImageFilter.GaussianBlur(self.px(7) * ss)))
            else:
                img = Image.new("RGBA", size, ImageColor.getrgb(border) + (0,))  # edges blend into the border colour
            ImageDraw.Draw(img).rounded_rectangle(box, radius * ss, fill=fill, outline=border, width=width * ss)
            img = img.resize((w + 2 * pad, h + 2 * pad), Image.LANCZOS)
            self.images[key] = (ImageTk.PhotoImage(img), pad)
        return self.images[key]

    def shape(self, x, y, w, h, radius, fill, border, **kw):
        photo, pad = self._shape(w, h, radius, self.colors[fill], self.colors[border], **kw)
        return self.canvas.create_image(x - pad, y - pad, image=photo, anchor="nw")

    def reshape(self, item, x, y, w, h, radius, fill, border, **kw):
        photo, pad = self._shape(w, h, radius, self.colors[fill], self.colors[border], **kw)
        self.canvas.coords(item, x - pad, y - pad)
        self.canvas.itemconfigure(item, image=photo)

    def _toggle_image(self, on: bool):
        key = ("toggle", on)
        if key not in self.images:
            ss, w, h = 4, self.px(40), self.px(20)
            col = self.colors
            img = Image.new("RGBA", (w * ss, h * ss), ImageColor.getrgb(col["accent" if on else "muted"]) + (0,))
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), h * ss // 2,
                                   fill=col["accent"] if on else col["card"], outline=col["accent" if on else "muted"], width=ss)
            r = (h - self.px(8)) * ss // 2
            cx, cy = (w * ss - h * ss // 2) if on else h * ss // 2, h * ss // 2
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col["on_accent" if on else "muted"])
            self.images[key] = (ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS)), 0)
        return self.images[key][0]

    def _bolt_image(self, color: str):
        key = ("bolt", color)
        if key not in self.images:
            self.images[key] = (ImageTk.PhotoImage(icon.bolt(color, self.px(18))), 0)
        return self.images[key][0]

    # --- building blocks ---

    def panel(self, y: int, h: int):
        m = self.px(MARGIN)
        self.shape(m, y, self.px(WIDTH) - 2 * m, h, self.px(10), "card", "border")

    def divider(self, y: int):
        m = self.px(MARGIN)
        self.canvas.create_line(m + 1, y, self.px(WIDTH) - m - 1, y, fill=self.colors["border"])

    def tile(self, x, y, size, char, color="accent"):
        """The rounded square with a device glyph."""
        self.shape(x, y, size, size, size // 4, "tile", "tile")
        self.glyph(x + size // 2, y + size // 2, char, round(size / self.scale * 0.46), color)

    def card(self, y, char, title, subtitle, command, value="", value_color="text", dimmed=False, toggle=None, charging=False):
        """A clickable row: lights up its border and lifts on hover.

        `toggle` is (value, on_change): a switch at the right edge instead of the chevron.
        `charging` puts a lightning bolt to the left of the value.
        """
        px, m = self.px, self.px(MARGIN)
        w, h, r = px(WIDTH) - 2 * m, px(68), px(10)
        normal = dict(fill="card", border="border", shadow=self.colors["shadow"] // 4)
        lifted = dict(fill="card", border="hover", shadow=self.colors["shadow"] // 2)
        item = self.shape(m, y, w, h, r, **normal)
        self.tile(m + px(12), y + px(12), px(44), char, "dim" if dimmed else "accent")
        right = px(70) if toggle else px(40)  # what the switch or the chevron takes
        value_w = self.measure(value, self.font(16, True)) if value else 0
        bolt_w = px(18) if value and charging else 0
        room = w - px(70) - right - px(4) - (value_w + bolt_w + px(10) if value else 0)
        self.text(m + px(70), y + px(13), self.fit(title, self.font(14, True), room), 14, True, "muted" if dimmed else "text")
        self.text(m + px(70), y + px(37), self.fit(subtitle, self.font(12), room), 12, color="dim" if dimmed else "muted")
        if value:
            self.text(m + w - right, y + h // 2, value, 16, True, value_color, anchor="e")
            if bolt_w:
                self.canvas.create_image(m + w - right - value_w - px(7), y + h // 2 + px(1),
                                         image=self._bolt_image(self.colors[value_color]), anchor="e")
        if toggle:
            self.toggle(y + h // 2, *toggle)  # its click area is registered first, so it wins over the card's
            self.hit(m, y, m + w, y + h, command,
                     lambda on: self.reshape(item, m, y, w, h, r, **(lifted if on else normal)))
            return
        self.glyph(m + w - px(22), y + h // 2, "", 11, "dim")
        self.hit(m, y, m + w, y + h, command,
                 lambda on: self.reshape(item, m, y, w, h, r, **(lifted if on else normal)))

    def crumbs(self, title: str | None = None) -> int:
        """The breadcrumb line; with no title it is the root page, where the only crumb is not a link."""
        px, m, y = self.px, self.px(MARGIN), self.px(18)
        font = self.font(13)
        if title is None:
            self.text(m, y, ROOT_TITLE, 13)
            return y + px(40)
        back = self.text(m, y, ROOT_TITLE, 13, color="accent")
        w = self.measure(ROOT_TITLE, font)
        self.hit(m - px(4), y - px(4), m + w + px(4), y + px(24), lambda: self.go("home"),
                 lambda on: self.canvas.itemconfigure(back, font=font + (("underline",) if on else ())))
        self.glyph(m + w + px(13), y + px(11), "", 9, "dim")
        room = px(WIDTH) - 2 * m - w - px(26)
        self.crumb_item = self.text(m + w + px(26), y, self.fit(title, font, room), 13)
        return y + px(40)

    def icon_button(self, cx: int, cy: int, char: str, command):
        """A glyph that gets a background on hover and dims for a moment after a click."""
        c, r = self.canvas, self.px(16)
        back = self.shape(cx - r, cy - r, 2 * r, 2 * r, self.px(8), "card", "border")
        c.itemconfigure(back, state="hidden")
        glyph = self.glyph(cx, cy, char, 15, "muted")

        def click():
            command()
            c.itemconfigure(glyph, fill=self.colors["dim"])
            self.root.after(900, lambda: c.itemconfigure(glyph, fill=self.colors["muted"]))

        self.hit(cx - r, cy - r, cx + r, cy + r, click,
                 lambda on: c.itemconfigure(back, state="normal" if on else "hidden"))

    def toggle(self, y_center: int, value: bool, on_change):
        px = self.px
        x = px(WIDTH) - px(MARGIN) - px(16) - px(40)
        self.canvas.create_image(x, y_center - px(10), image=self._toggle_image(value), anchor="nw")
        self.hit(x - px(6), y_center - px(16), x + px(46), y_center + px(16), lambda: on_change(not value))

    def _segment_rows(self, options) -> list[list]:
        """Splits the pills into lines that fit the panel: [[(value, label, x, width), ...], ...]."""
        px, font = self.px, self.font(12)
        start, limit = px(MARGIN + 16), px(WIDTH - MARGIN - 16)
        rows, x = [[]], start
        for value, label in options:
            w = self.measure(label, font) + px(22)
            if x + w > limit and rows[-1]:
                rows.append([])
                x = start
            rows[-1].append((value, label, x, w))
            x += w + px(6)
        return rows

    def segments(self, y: int, options, current, on_pick):
        """One-of-many choice drawn as pills. options: (value, label)."""
        h = self.px(30)
        for i, row in enumerate(self._segment_rows(options)):
            self._segment_row(y + i * self.px(36), h, row, current, on_pick)

    def _segment_row(self, y, h, row, current, on_pick):
        for value, label, x, w in row:
            item = self.shape(x, y, w, h, h // 2, *(("accent", "accent") if value == current else ("card", "border")))
            self.text(x + w // 2, y + h // 2, label, 12, color="on_accent" if value == current else "text", anchor="center")
            if value != current:
                self.hit(x, y, x + w, y + h, lambda v=value: on_pick(v),
                         lambda on, i=item, x=x, w=w: self.reshape(i, x, y, w, h, h // 2, "card", "hover" if on else "border"))

    def choices(self, y: int, rows) -> int:
        """A panel of settings: rows of (label, options, current, on_pick, extra). Returns its bottom.

        `options` are the pills of a one-of-many choice; `extra` is None or (height, draw(top)) for a control below them.
        """
        px = self.px

        def lines(options):
            return len(self._segment_rows(options)) if options else 0

        heights = [px(46) + lines(row[1]) * px(36) + (row[4][0] if row[4] else 0) for row in rows]
        self.panel(y, sum(heights))
        for i, ((label, options, current, on_pick, extra), height) in enumerate(zip(rows, heights)):
            if i:
                self.divider(y)
            self.text(px(MARGIN + 16), y + px(12), label, 13)
            self.segments(y + px(40), options, current, on_pick)
            if extra:
                extra[1](y + px(40) + lines(options) * px(36))
            y += height
        return y

    def levels_row(self, title, options, current, on_pick, low, warn, on_change):
        """A row with the two-handle slider under its pills."""
        return title, options, current, on_pick, (self.px(52), lambda top: self.slider(top + self.px(28), low, warn, on_change))

    def show_row(self, title, options, current, on_pick, when, low, warn, on_change):
        """A row with three battery tiles: a click lights the tile and every worse one to its left."""
        pad = self.px(6) if options else 0  # air between the pills and the tiles
        return title, options, current, on_pick, (self.px(86) + pad, lambda top: self.batteries(top + pad, when, low, warn, on_change))

    def _battery_image(self, fill: str, level: float):
        key = ("battery", fill, level)
        if key not in self.images:
            ss, w, h = 4, self.px(16), self.px(30)
            line = max(1, round(self.scale * 1.5)) * ss
            outline = self.colors["text"]
            img = Image.new("RGBA", (w * ss, h * ss), ImageColor.getrgb(outline) + (0,))
            draw = ImageDraw.Draw(img)
            top = self.px(4) * ss
            draw.rounded_rectangle((w * ss * 0.3, 0, w * ss * 0.7, top), ss, fill=outline)
            draw.rounded_rectangle((0, top - 1, w * ss - 1, h * ss - 1), self.px(3) * ss, outline=outline, width=line)
            inner_top, inner_bottom = top + line * 2, h * ss - 1 - line * 2
            draw.rounded_rectangle((line * 2, inner_bottom - (inner_bottom - inner_top) * level, w * ss - 1 - line * 2, inner_bottom),
                                   ss, fill=fill)
            self.images[key] = (ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS)), 0)
        return self.images[key][0]

    def batteries(self, y: int, when: str, low: int, warn: int, on_change):
        px, c = self.px, self.canvas
        w, h, gap = px(96), px(74), px(8)
        lit = {"low": 1, "warn": 2}.get(when, 3)
        tiles = (("low", icon.LOW_COLOR, 0.25, f"до {low}%"), ("warn", icon.WARN_COLOR, 0.55, f"до {warn}%"),
                 ("always", self.colors["text"], 1.0, f"понад {warn}%"))
        x = px(MARGIN + 16)
        for i, (value, fill, level, caption) in enumerate(tiles):
            on = i < lit
            look = ("tile", "accent") if on else ("card", "border")
            item = self.shape(x, y, w, h, px(8), *look)
            c.create_image(x + w // 2, y + px(9), image=self._battery_image(fill, level), anchor="n")
            self.text(x + w // 2, y + h - px(13), caption, 10, color="text" if on else "muted", anchor="center")
            self.hit(x, y, x + w, y + h, lambda v=value: on_change(v),
                     lambda hover, i=item, x=x, look=look: self.reshape(
                         i, x, y, w, h, px(8), look[0], "hover" if hover and look[1] == "border" else look[1]))
            x += w + gap

    def slider(self, cy: int, low: int, warn: int, on_change):
        """One track with two handles, in steps of 5%: the battery is red up to the first and orange up to the second."""
        c, px = self.canvas, self.px
        r = px(9)
        x0, x1 = px(MARGIN + 16) + r, px(WIDTH - MARGIN - 16) - r
        values, active = [low, warn], [None]

        def pos(v):
            return round(x0 + (x1 - x0) * v / 100)

        def line(color):
            return c.create_line(x0, cy, x1, cy, fill=color, width=px(6), capstyle="round")

        line(self.colors["border"])
        bars = [line(icon.LOW_COLOR), line(icon.WARN_COLOR)]
        c.tag_raise(bars[0])
        knobs = [self.shape(0, 0, 2 * r, 2 * r, r, "card", "muted") for _ in values]
        labels = [self.text(0, 0, "", 12, color="muted", anchor="s") for _ in values]

        def draw():
            for bar, knob, v in zip(bars, knobs, values):
                c.coords(bar, x0, cy, pos(v), cy)
                c.itemconfigure(bar, state="normal" if v else "hidden")
                c.coords(knob, pos(v) - r, cy - r)
            xs = [pos(v) for v in values]
            close = xs[1] - xs[0] < px(40)  # the two labels would overlap: put them side by side
            middle = sum(xs) // 2
            for label, x, v, anchor, shift in zip(labels, xs, values, ("se", "sw"), (-px(3), px(3))):
                c.coords(label, middle + shift if close else x, cy - px(13))
                c.itemconfigure(label, text=f"{v}%", anchor=anchor if close else "s")

        def move(x, done=False):
            raw = (x - x0) / (x1 - x0) * 100
            if active[0] is None:  # grab the nearer handle; of two in one spot, the one on the pointer's side
                if values[0] == values[1]:
                    active[0] = 0 if raw < values[0] else 1
                else:
                    active[0] = 0 if abs(raw - values[0]) <= abs(raw - values[1]) else 1
            v = min(100, max(0, round(raw / 5) * 5))
            values[active[0]] = min(v, values[1]) if active[0] == 0 else max(v, values[0])
            draw()
            if done and values != [low, warn]:
                on_change(*values)

        def start():
            self.drag = move
            move(self.at[0])

        draw()
        self.hit(x0 - r, cy - px(14), x1 + r, cy + px(14), start)

    def _find(self, key):
        return next((d for d in self.snap if d["key"] == key), None)

    @staticmethod
    def _status(d: dict, percent: bool = False) -> str:
        parts = [d["kind"], "підключено" if d["connected"] else "не підключено"]
        if d["connected"] and d["charging"]:
            parts.append("заряджається")
        if percent and d["percent"] is not None:
            parts.append(f"{d['percent']}%")
        return " · ".join(parts)

    # --- pages ---

    def _home(self, _):
        px, m = self.px, self.px(MARGIN)
        y = self.crumbs()
        self.icon_button(px(WIDTH) - m - px(16), px(29), "", self.app.refresh_now)
        devices = sorted(self.snap, key=lambda d: (not d["connected"], not d["enabled"], d["title"].lower()))
        if not devices:
            self.text(m, y, "Пристроїв поки не знайдено. Щойно якийсь підключиться — він з'явиться тут.", 12, color="muted")
            y += px(36)
        for d in devices:
            low, warn = d["levels"]
            if not d["connected"] or d["percent"] is None:
                color = "dim"
            elif d["charging"]:
                color = "good"
            else:
                color = "low" if d["percent"] <= low else "warn" if d["percent"] <= warn else "text"
            self.card(y, devtype.TYPES[d["type"]][1], d["title"], self._status(d),
                      lambda key=d["key"]: self.go("device", key),
                      value="" if d["percent"] is None else f"{d['percent']}%", value_color=color, dimmed=not d["connected"],
                      charging=d["connected"] and d["charging"],
                      toggle=(d["enabled"], lambda v, key=d["key"]: (self.app.update_device(key, enabled=v), self.changed())))
            y += px(78)
        y += px(14)
        self.card(y, "", "Налаштування",
                  "Типові значення для всіх пристроїв" + (", автозапуск" if autostart.AVAILABLE else ""),
                  lambda: self.go("settings"))
        return y + px(68)

    def _device(self, key):
        d = self._find(key)
        if d is None:
            self.page = ("home", None)
            return self._home(None)
        px, m, c = self.px, self.px(MARGIN), self.canvas
        w = px(WIDTH) - 2 * m
        defaults = self.app.settings
        y = self.crumbs(d["title"])

        self.tile(m, y, px(56), devtype.TYPES[d["type"]][1])
        title_font = self.font(18, True)
        title_item = self.text(m + px(72), y + px(4), self.fit(d["title"], title_font, w - px(72)), 18, True)
        self.status_item = self.text(m + px(72), y + px(33), self._status(d, percent=True), 12, color="muted")
        y += px(76)

        # tray + name
        row = px(60)
        self.panel(y, 2 * row)
        self.divider(y + row)
        self.text(m + px(16), y + px(11), "Показувати в треї", 13)
        self.text(m + px(16), y + px(33), "Окрема іконка з рівнем заряду", 11, color="muted")
        self.toggle(y + row // 2, d["enabled"], lambda v: (self.app.update_device(key, enabled=v), self.changed()))
        y += row
        self.text(m + px(16), y + px(11), "Назва", 13)
        self.text(m + px(16), y + px(33), "Задайте свою, якщо хочете", 11, color="muted")
        name = tk.StringVar(value=d["label"])
        col = self.colors
        field = tk.Frame(c, bg=col["bg"], highlightthickness=1, highlightbackground=col["border"], highlightcolor=col["accent"])
        entry = tk.Entry(field, textvariable=name, font=self.font(13), relief="flat", bd=0, highlightthickness=0,
                         bg=col["bg"], fg=col["text"], insertbackground=col["text"])
        entry.pack(fill="both", expand=True, padx=px(8))
        # Tk 8.6 has no placeholder: a label over the empty field shows the device's own name.
        hint = tk.Label(field, text=self.fit(d["name"], self.font(13), px(176)), font=self.font(13), bg=col["bg"], fg=col["dim"],
                        bd=0, padx=0, pady=0, cursor="xterm")
        hint.bind("<Button-1>", lambda e: entry.focus_set())

        def place_hint(*_):
            if name.get():
                hint.place_forget()
            else:
                hint.place(x=px(8) + 3, rely=0.5, anchor="w")

        name.trace_add("write", place_hint)
        place_hint()
        entry.bind("<FocusIn>", lambda e: field.config(highlightbackground=col["accent"]))
        entry.bind("<FocusOut>", lambda e: field.config(highlightbackground=col["border"]), add="+")
        self.widgets.append(field)
        c.create_window(m + w - px(16), y + row // 2, window=field, anchor="e", width=px(200), height=px(32))
        saved = [d["label"]]

        def commit():
            text = name.get().strip()
            label = "" if text == d["name"] else text
            if label == saved[0]:
                return
            saved[0] = label
            self.app.update_device(key, label=label)
            self.snap = self.app.snapshot()
            c.itemconfigure(title_item, text=self.fit(label or d["name"], title_font, w - px(72)))
            c.itemconfigure(self.crumb_item, text=self.fit(label or d["name"], self.font(13), w - px(120)))

        entry.bind("<Return>", lambda e: c.focus_set())
        entry.bind("<FocusOut>", lambda e: commit(), add="+")
        self.before_leave = commit
        y += row + px(14)

        # icon
        gap = px(6)
        options = [("", "Авто")] + [(kind, label) for kind, (label, _) in devtype.TYPES.items()]
        size = (w - px(32) - gap * (len(options) - 1)) // len(options)
        self.panel(y, px(46) + size + px(16))
        self.text(m + px(16), y + px(12), "Іконка", 13)
        chosen = devtype.TYPES[d["type"]][0]
        self.text(m + w - px(16), y + px(14), chosen if d["icon"] else f"Визначено автоматично: {chosen}", 11,
                  color="muted", anchor="ne")
        x, ty = m + px(16), y + px(46)
        for kind, label in options:
            char = devtype.TYPES[kind or d["detected"]][1]
            selected = kind == d["icon"]
            item = self.shape(x, ty, size, size, px(8), "tile" if selected else "card", "accent" if selected else "border")
            if kind:
                self.glyph(x + size // 2, ty + size // 2, char, 19, "accent" if selected else "text")
            else:
                self.glyph(x + size // 2, ty + size // 2 - px(6), char, 16, "accent" if selected else "text")
                self.text(x + size // 2, ty + size - px(11), "авто", 9, color="muted", anchor="center")
            if not selected:
                self.hit(x, ty, x + size, ty + size, lambda k=kind: (self.app.update_device(key, icon=k), self.changed()),
                         lambda on, i=item, x=x: self.reshape(i, x, ty, size, size, px(8), "card", "hover" if on else "border"))
            x += size + gap
        y += px(46) + size + px(16) + px(22)

        # overrides
        self.text(m, y, "Власні налаштування", 12, True, "muted")
        y += px(26)

        def override(name, title, choices, fmt):
            options = [(None, f"Типове ({fmt(defaults[name])})")] + _options(choices, d[name], fmt)
            return title, options, d[name], lambda v: (self.app.update_device(key, **{name: v}), self.changed()), None

        def levels(low, warn):
            self.app.update_device(key, low_percent=low, warn_percent=warn)
            self.changed()

        own = d["low_percent"] is not None or d["warn_percent"] is not None
        low, warn = d["levels"]
        usual = f"Типовий ({defaults['low_percent']}% / {max(defaults['low_percent'], defaults['warn_percent'])}%)"
        pick = lambda v: levels(*((low, warn) if v else (None, None)))
        color = self.levels_row(COLOR_TITLE, [(False, usual), (True, "Власний")], own, pick, low, warn, levels) if own \
            else (COLOR_TITLE, [(False, usual), (True, "Власний")], own, pick, None)

        def show(value):
            self.app.update_device(key, show_when=value)
            self.changed()

        when = defaults["show_when"] if d["show_when"] is None else d["show_when"]
        shown = self.show_row(SHOW_TITLE, [(False, "Типове"), (True, "Власне")], d["show_when"] is not None,
                              lambda v: show(when if v else None), when, low, warn, show)
        return self.choices(y, [shown, color] + [override(*row) for row in OPTIONS])

    def _settings(self, _):
        px, m = self.px, self.px(MARGIN)
        s = self.app.settings
        y = self.crumbs("Налаштування")
        self.text(m, y, "Налаштування", 22, True)
        y += px(46)
        if autostart.AVAILABLE:
            self.panel(y, px(56))
            self.text(m + px(16), y + px(18), "Запускати разом із Windows", 13)
            self.toggle(y + px(28), autostart.is_enabled(), lambda v: (autostart.toggle(), self.render()))
            y += px(56) + px(14)

        def theme(value):
            self.app.update_settings(theme=value)
            self._apply_theme()
            self.changed()

        y = self.choices(y, [("Тема", [("auto", "Авто"), ("light", "Світла"), ("dark", "Темна")], s["theme"], theme, None)])
        y += px(22)
        self.text(m, y, "Типові значення", 12, True, "muted")
        self.text(px(WIDTH) - m, y + px(1), "для пристрою змінюються на його сторінці", 11, color="dim", anchor="ne")
        y += px(26)

        def default(name, title, choices, fmt):
            options = _options(choices, s[name], fmt)
            return title, options, s[name], lambda v: (self.app.update_settings(**{name: v}), self.changed()), None

        def levels(low, warn):
            self.app.update_settings(low_percent=low, warn_percent=warn)
            self.changed()

        rows = [default(*row) for row in OPTIONS + (("poll_seconds", "Оновлювати заряд кожні", POLL_CHOICES, "{} с".format),)]
        low, warn = s["low_percent"], max(s["low_percent"], s["warn_percent"])
        show = lambda value: (self.app.update_settings(show_when=value), self.changed())
        y = self.choices(y, [self.show_row(SHOW_TITLE, (), None, None, s["show_when"], low, warn, show),
                             self.levels_row(COLOR_TITLE, (), None, None, low, warn, levels)] + rows)
        self.text(m, y + px(14), "Зміни зберігаються одразу.", 11, color="dim")
        return y + px(34)
