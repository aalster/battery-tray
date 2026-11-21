"""One tray icon per device, each with a stable identity so Windows remembers its position."""
import ctypes
import sys
import threading
import uuid

import pystray
from pystray._util import win32

from . import autostart

_NAMESPACE = uuid.UUID("6d0f3c1e-9a52-4f6b-8a57-3f0f5b7c2a10")


class _GuidIcon(pystray.Icon):
    """pystray registers icons anonymously, so Windows forgets where the user dragged them.

    Registering with a GUID makes the position and the "show on taskbar" choice stick.
    """

    def __init__(self, identity: str, *args, **kwargs):
        # Windows ties a GUID to the path of the executable that registered it, so the path is part of it.
        raw = uuid.uuid5(_NAMESPACE, f"{identity}\0{sys.executable.lower()}").bytes_le
        self._guid = win32.NOTIFYICONDATAW.GUID.from_buffer_copy(raw)
        self._use_guid = True
        super().__init__(*args, **kwargs)

    def _message(self, code, flags, **kwargs):
        if self._use_guid:
            flags |= win32.NIF_GUID
            kwargs["guidItem"] = self._guid
        win32.Shell_NotifyIcon(code, win32.NOTIFYICONDATAW(
            cbSize=ctypes.sizeof(win32.NOTIFYICONDATAW), hWnd=self._hwnd, uFlags=flags, **kwargs))

    def _show(self):
        try:  # a crashed previous run may have left an icon with this GUID behind
            self._message(win32.NIM_DELETE, 0)
        except OSError:
            pass
        try:
            super()._show()
        except OSError:  # GUID is taken by another executable: fall back to an anonymous icon
            self._use_guid = False
            super()._show()


class TrayIcon:
    def __init__(self, app, identity: str, image, title: str):
        self.app = app
        self.title = title
        self.shown = None  # state currently displayed
        self.icon = None
        ready = threading.Event()
        threading.Thread(target=self._run, args=(identity, image, ready), daemon=True).start()
        ready.wait(5)

    def _run(self, identity, image, ready):
        # pystray creates the window in the constructor, so it must live in the loop's thread.
        from .app import MAIN

        menu = pystray.Menu(
            pystray.MenuItem(lambda _: self.title, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Приховати цей пристрій", lambda: self.app.hide(identity), visible=identity != MAIN),
            pystray.MenuItem("Налаштування…", lambda: self.app.ui_queue.put("settings"), default=True),
            pystray.MenuItem("Оновити зараз", lambda: self.app.refresh_now()),
            pystray.MenuItem("Запускати разом із Windows", lambda: autostart.toggle(),
                             checked=lambda _: autostart.is_enabled(), visible=autostart.AVAILABLE),
            pystray.MenuItem("Вихід", lambda: self.app.quit()),
        )
        self.icon = _GuidIcon(identity, "battery-tray", image, self.title, menu)
        ready.set()
        self.icon.run()

    def update(self, image, title: str):
        self.title = title
        self.icon.icon = image
        self.icon.title = title
        self.icon.update_menu()

    def stop(self):
        if self.icon:
            self.icon.stop()
