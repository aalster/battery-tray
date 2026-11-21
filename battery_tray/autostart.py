"""Start with Windows. Only the built exe can register itself; a run from source cannot."""
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "BatteryTray"
AVAILABLE = bool(getattr(sys, "frozen", False))


def _command() -> str:
    return f'"{sys.executable}" --tray'  # --tray: stay in the tray, do not open the window


def is_enabled() -> bool:
    if not AVAILABLE:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, VALUE)[0] == _command()
    except OSError:
        return False


def toggle():
    if not AVAILABLE:
        return
    enabled = is_enabled()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.DeleteValue(k, VALUE)
        else:
            winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, _command())
