import ctypes
import logging
import os
import queue
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import Reading, bluetooth, devtype, icon, settings
from .logitech import LogitechSource

log = logging.getLogger("battery_tray")
MAIN = "\0main"  # identity of the app's own tray icon
SHOW_EVENT = "Local\\BatteryTrayShowWindow"


class App:
    def __init__(self):
        self.settings = settings.load()
        self.lock = threading.RLock()
        self.logitech = LogitechSource()
        self.current: dict[str, Reading] = {}  # latest poll
        self.last_live = {}                    # key -> (percent, monotonic time last seen connected)
        self.icons = {}                        # display name -> TrayIcon
        self.rescan = False                    # a full scan for new devices was asked for
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.ui_queue = queue.Queue()

    # --- data ---

    def read(self) -> list[Reading]:
        readings = []
        for source in (self.logitech.poll, bluetooth.poll):
            try:
                readings += source()
            except Exception:
                log.exception("source failed")
        return readings

    def snapshot(self) -> list[dict]:
        """Every device ever found, with its current state, for the settings window."""
        with self.lock:
            out = []
            for key, d in self.settings["devices"].items():
                r = self.current.get(key)
                out.append({
                    "key": key, "name": d["name"], "kind": d["kind"], "label": d.get("label", ""),
                    "title": settings.display_name(d), "enabled": d.get("enabled", False),
                    "connected": bool(r and r.connected), "percent": r.percent if r else None,
                    "charging": bool(r and r.charging),
                    "type": devtype.resolve(d), "detected": devtype.resolve(dict(d, icon="")), "icon": d.get("icon", ""),
                    "low_percent": d.get("low_percent"), "warn_percent": d.get("warn_percent"),
                    "linger_minutes": d.get("linger_minutes"), "show_when": d.get("show_when"),
                    "levels": self._levels(key),
                })
            return out

    def refresh_now(self):
        """Re-reads the charge right away, then looks for new devices."""
        # The scan for new devices waits seconds for the silent receiver slots, so the known devices are read first.
        self.logitech.polls = 1  # makes the next poll a quick one
        self.rescan = True
        self.wake.set()

    def update_device(self, key: str, **changes):
        """Called by the settings window. An override set to None falls back to the default."""
        with self.lock:
            if key in self.settings["devices"]:
                self.settings["devices"][key].update(changes)
                settings.save(self.settings)
        self.wake.set()

    def hide(self, name: str):
        """Called from a device icon's menu: stops showing every device behind that icon."""
        with self.lock:
            for d in self.settings["devices"].values():
                if settings.display_name(d) == name:
                    d["enabled"] = False
            settings.save(self.settings)
        self.wake.set()

    def update_settings(self, **changes):
        with self.lock:
            self.settings.update(changes)
            settings.save(self.settings)
        self.wake.set()

    def _option(self, key: str, name: str):
        """A device's own value of a setting, or the default one."""
        value = self.settings["devices"][key].get(name)
        return self.settings[name] if value is None else value

    def _levels(self, key: str) -> tuple[int, int]:
        """(red up to, orange up to) for a device."""
        low = self._option(key, "low_percent")
        return low, max(low, self._option(key, "warn_percent"))

    # --- tray ---

    def _wanted(self, now: float) -> dict:
        """display name -> (device type, percent, fill color, outline color, tooltip). Same-named devices share one icon."""
        devices = self.settings["devices"]
        groups = {}
        for key, d in devices.items():
            if d.get("enabled"):
                groups.setdefault(settings.display_name(d), []).append(key)
        theme = icon.theme_color()
        wanted = {MAIN: (MAIN, None, theme, theme, "Battery Tray")}  # the app's own icon is always there
        for name, keys in groups.items():
            live = [self.current[k] for k in keys if k in self.current and self.current[k].connected]
            if live:
                r = live[0]
                if not self._shown(r.key, r.percent):
                    continue
                if r.charging:
                    fill, note = icon.CHARGING_COLOR, " (заряджається)"
                else:
                    fill, note = self._fill(r.key, r.percent, theme), ""
                wanted[name] = (devtype.resolve(devices[r.key]), r.percent, fill, theme, f"{name}: {r.percent}%{note}")
                continue
            recent = [(self.last_live[k][1], k) for k in keys
                      if k in self.last_live and now - self.last_live[k][1] < self._option(k, "linger_minutes") * 60]
            if recent:
                key = max(recent)[1]
                percent = self.last_live[key][0]
                if self._shown(key, percent):
                    # Looks the same as when connected: a mouse that fell asleep must not make its icon flicker.
                    wanted[name] = (devtype.resolve(devices[key]), percent, self._fill(key, percent, theme), theme,
                                    f"{name}: {percent}% (відключено)")
        return wanted

    def _shown(self, key: str, percent: int) -> bool:
        """Whether the device's icon is wanted at this charge."""
        when = self._option(key, "show_when")
        low, warn = self._levels(key)
        return percent <= (low if when == "low" else warn) or when not in ("low", "warn")

    def _fill(self, key: str, percent: int, normal: str) -> str:
        low, warn = self._levels(key)
        return icon.LOW_COLOR if percent <= low else icon.WARN_COLOR if percent <= warn else normal

    def refresh(self):
        from .tray import TrayIcon

        readings = self.read()
        now = time.monotonic()
        with self.lock:
            self.current = {r.key: r for r in readings}
            new = False
            for r in readings:
                if r.key not in self.settings["devices"]:
                    self.settings["devices"][r.key] = {"name": r.name, "kind": r.kind, "label": "", "enabled": False}
                    new = True
                if r.type and self.settings["devices"][r.key].get("type") != r.type:
                    self.settings["devices"][r.key]["type"] = r.type
                    new = True
                if r.connected and r.percent is not None:
                    self.last_live[r.key] = (r.percent, now)
            if new:
                settings.save(self.settings)
            wanted = self._wanted(now)

        for name in [n for n in self.icons if n not in wanted]:
            self.icons.pop(name).stop()
        for name, state in wanted.items():
            kind, percent, fill, outline, title = state
            tray = self.icons.get(name)
            if tray is None or tray.shown != state:
                image = icon.app_icon(outline) if kind == MAIN else icon.render(devtype.TYPES[kind][1], percent, fill, outline)
            if tray is None:
                tray = self.icons[name] = TrayIcon(self, name, image, title)
            elif tray.shown != state:
                tray.update(image, title)
            tray.shown = state

    def quit(self):
        self.stopping.set()
        self.wake.set()

    def poll_loop(self):
        first = True
        while not self.stopping.is_set():
            try:
                self.refresh()
            except Exception:
                log.exception("refresh failed")
            self.ui_queue.put("update")  # the window shows the new state without waiting for its timer
            if self.rescan:
                self.rescan = False
                self.logitech.polls = 0  # makes the next poll a full scan
                continue
            if first and not any(d.get("enabled") for d in self.settings["devices"].values()):
                self.ui_queue.put("settings")  # nothing chosen yet: let the user pick
            first = False
            self.wake.wait(max(5, self.settings["poll_seconds"]))
            self.wake.clear()
        for tray in self.icons.values():
            tray.stop()
        self.ui_queue.put("quit")


def _setup_logging():
    folder = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "battery-tray"
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / "battery-tray.log", maxBytes=200_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    threading.excepthook = lambda a: log.error("thread %s crashed", a.thread, exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def main():
    app = App()
    if "--once" in sys.argv:  # print everything found to the console and exit
        for r in app.read():
            state = "connected" if r.connected else "disconnected"
            print(f"{r.percent:>3}%  {r.name}  [{r.kind}, {state}{', charging' if r.charging else ''}]")
        return
    _setup_logging()
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = k32.CreateEventW.restype = k32.OpenEventW.restype = ctypes.c_void_p
    k32.SetEvent.argtypes = [ctypes.c_void_p]
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    mutex = k32.CreateMutexW(None, False, "Local\\BatteryTraySingleInstance")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS: ask the running instance to show its window
        running = k32.OpenEventW(0x0002, False, SHOW_EVENT)  # EVENT_MODIFY_STATE
        if running:
            k32.SetEvent(running)
        return
    show = k32.CreateEventW(None, False, False, SHOW_EVENT)

    def watch_show():
        while k32.WaitForSingleObject(show, 0xFFFFFFFF) == 0:
            app.ui_queue.put("settings")

    threading.Thread(target=watch_show, daemon=True).start()
    if "--tray" not in sys.argv:  # started by hand, not by autostart: show the window
        app.ui_queue.put("settings")
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp settings window
    except (OSError, AttributeError):
        pass
    log.info("started")
    from .gui import SettingsWindow

    window = SettingsWindow(app)
    threading.Thread(target=app.poll_loop, daemon=True).start()
    try:
        window.run()
    finally:
        app.quit()
        del mutex
        log.info("stopped")
