import ctypes
import logging
import os
import queue
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import Reading, bluetooth, icon, settings
from .logitech import LogitechSource

log = logging.getLogger("battery_tray")
IDLE = "\0idle"


class App:
    def __init__(self):
        self.settings = settings.load()
        self.lock = threading.RLock()
        self.logitech = LogitechSource()
        self.current: dict[str, Reading] = {}  # latest poll
        self.last_live = {}                    # key -> (percent, monotonic time last seen connected)
        self.icons = {}                        # display name -> TrayIcon
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
                    "enabled": d.get("enabled", False),
                    "connected": bool(r and r.connected), "percent": r.percent if r else None,
                })
            return out

    def apply(self, devices: dict, linger_minutes: int):
        """Called by the settings window."""
        with self.lock:
            for key, changes in devices.items():
                if key in self.settings["devices"]:
                    self.settings["devices"][key].update(changes)
            self.settings["linger_minutes"] = linger_minutes
            settings.save(self.settings)
        self.wake.set()

    # --- tray ---

    def _wanted(self, now: float) -> dict:
        """display name -> (text, color, tooltip). Same-named devices share one icon."""
        groups = {}
        for key, d in self.settings["devices"].items():
            if d.get("enabled"):
                groups.setdefault(settings.display_name(d), []).append(key)
        linger = self.settings["linger_minutes"] * 60
        wanted = {}
        for name, keys in groups.items():
            live = [self.current[k] for k in keys if k in self.current and self.current[k].connected]
            if live:
                r = live[0]
                if r.charging:
                    color, note = icon.CHARGING_COLOR, " (заряджається)"
                elif r.percent <= self.settings["low_percent"]:
                    color, note = icon.LOW_COLOR, ""
                else:
                    color, note = icon.theme_color(), ""
                wanted[name] = (str(r.percent), color, f"{name}: {r.percent}%{note}")
                continue
            recent = [self.last_live[k] for k in keys if k in self.last_live and now - self.last_live[k][1] < linger]
            if recent:
                percent = max(recent, key=lambda x: x[1])[0]
                wanted[name] = (str(percent), icon.STALE_COLOR, f"{name}: {percent}% (відключено)")
        if not wanted:  # keep one icon so the menu stays reachable
            wanted[IDLE] = ("--", icon.theme_color(), "Battery Tray: немає пристроїв для показу")
        return wanted

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
                if r.connected and r.percent is not None:
                    self.last_live[r.key] = (r.percent, now)
            if new:
                settings.save(self.settings)
            wanted = self._wanted(now)

        for name in [n for n in self.icons if n not in wanted]:
            self.icons.pop(name).stop()
        for name, state in wanted.items():
            text, color, title = state
            tray = self.icons.get(name)
            if tray is None:
                tray = self.icons[name] = TrayIcon(self, name, icon.render(text, color), title)
            elif tray.shown != state:
                tray.update(icon.render(text, color), title)
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
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\BatteryTraySingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return
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
