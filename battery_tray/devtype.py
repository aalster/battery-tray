"""Device types: what the icon picker offers, and how a type is guessed."""

OTHER = "other"

# id -> (label, glyph in Segoe Fluent Icons / Segoe MDL2 Assets)
TYPES = {
    "headphones": ("Навушники", ""),
    "mouse": ("Миша", ""),
    "keyboard": ("Клавіатура", ""),
    "gamepad": ("Геймпад", ""),
    "speaker": ("Колонка", ""),
    "phone": ("Телефон", ""),
    OTHER: ("Інше", ""),
}

_NAME_HINTS = (
    ("gamepad", ("controller", "gamepad", "xbox", "dualsense", "dualshock", "joy-con", "joystick")),
    ("keyboard", ("keyboard", "keys", "клавіат")),
    ("mouse", ("mouse", "mx master", "mx anywhere", "trackball", "миш")),
    ("headphones", ("headphone", "headset", "earbud", "buds", "airpods", "навушник")),
    ("speaker", ("speaker", "soundbar", "колонк")),
    ("phone", ("phone", "iphone", "galaxy", "pixel")),
)


def from_name(name: str) -> str:
    low = name.lower()
    for kind, words in _NAME_HINTS:
        if any(w in low for w in words):
            return kind
    return ""


def from_class_of_device(cod: int) -> str:
    """Bluetooth Classic "Class of Device"."""
    major, minor = (cod >> 8) & 0x1F, (cod >> 2) & 0x3F
    if major == 2:
        return "phone"
    if major == 4:
        return "speaker" if minor in (5, 7, 10) else "headphones"
    if major == 5:
        if minor & 0x0F in (1, 2):
            return "gamepad"
        return {1: "keyboard", 2: "mouse", 3: "keyboard"}.get(minor >> 4, "")
    return ""


def from_hid_usages(usages: set[int]) -> str:
    """Generic Desktop usages of the device's top-level HID collections."""
    if usages & {4, 5}:
        return "gamepad"
    if 6 in usages:
        return "keyboard"
    if 2 in usages:
        return "mouse"
    return ""


def from_hidpp(kind: int) -> str:
    """HID++ 2.0 feature 0x0005, getDeviceType."""
    return {0: "keyboard", 2: "keyboard", 3: "mouse", 4: "mouse", 5: "mouse",
            8: "headphones", 11: "gamepad", 12: "gamepad", 14: "speaker"}.get(kind, "")


def resolve(device: dict) -> str:
    """The type to show for a stored device: the user's choice, else the detected one."""
    kind = device.get("icon") or device.get("type") or from_name(device.get("label") or device["name"])
    return kind if kind in TYPES else OTHER
