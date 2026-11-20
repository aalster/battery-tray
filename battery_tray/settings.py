"""User settings, edited through the GUI and stored in %APPDATA%\\battery-tray\\settings.json."""
import json
import os
from pathlib import Path

PATH = Path(os.environ.get("APPDATA", Path.home())) / "battery-tray" / "settings.json"

DEFAULTS = {
    "poll_seconds": 30,
    "low_percent": 20,
    "linger_minutes": 5,   # keep showing a device this long after it disconnects
    "devices": {},         # key -> {"name", "kind", "label", "enabled"}
}


def load() -> dict:
    data = dict(DEFAULTS, devices={})
    try:
        data.update(json.loads(PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return data


def save(data: dict):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def display_name(device: dict) -> str:
    return device.get("label") or device["name"]
