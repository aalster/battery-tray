"""User settings, edited through the GUI and stored in %APPDATA%\\battery-tray\\settings.json."""
import json
import os
from pathlib import Path

PATH = Path(os.environ.get("APPDATA", Path.home())) / "battery-tray" / "settings.json"

DEFAULTS = {
    "poll_seconds": 30,
    "low_percent": 20,     # the battery in the tray is red up to this charge
    "warn_percent": 40,    # ... and orange up to this one
    "linger_minutes": 5,   # keep showing a device this long after it disconnects
    "show_when": "always", # show a device's icon: "low" - only while red, "warn" - red or orange, "always"
    # key -> {"name", "kind", "label", "enabled", "type" (detected), "icon" (chosen type),
    #         "low_percent" / "warn_percent" / "linger_minutes" / "show_when" (optional, override the defaults above)}
    "devices": {},
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
