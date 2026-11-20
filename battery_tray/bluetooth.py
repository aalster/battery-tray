"""Battery of paired Bluetooth devices, as Windows itself reports it (PnP device properties)."""
import ctypes
import re
import uuid
from ctypes import wintypes

from . import Reading

_cfg = ctypes.WinDLL("cfgmgr32")

CR_SUCCESS = 0
CM_GETIDLIST_FILTER_ENUMERATOR = 0x1
CM_GETIDLIST_FILTER_PRESENT = 0x100


class _DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", ctypes.c_ubyte * 16), ("pid", wintypes.ULONG)]


def _key(guid: str, pid: int) -> _DEVPROPKEY:
    return _DEVPROPKEY((ctypes.c_ubyte * 16)(*uuid.UUID(guid).bytes_le), pid)


KEY_BATTERY = _key("104EA319-6EE2-4701-BD47-8DDBF425BBE5", 2)
KEY_CONNECTED = _key("83DA6326-97A6-4088-9453-A1923F573B29", 15)
KEY_FRIENDLY_NAME = _key("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)
KEY_NAME = _key("B725F130-47EF-101A-A5F1-02608C9EEBAC", 10)

_cfg.CM_Get_Device_ID_List_SizeW.argtypes = [ctypes.POINTER(wintypes.ULONG), wintypes.LPCWSTR, wintypes.ULONG]
_cfg.CM_Get_Device_ID_ListW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG]
_cfg.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.LPCWSTR, wintypes.ULONG]
_cfg.CM_Get_DevNode_PropertyW.argtypes = [
    wintypes.DWORD, ctypes.POINTER(_DEVPROPKEY), ctypes.POINTER(wintypes.ULONG),
    ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.ULONG,
]


def _device_ids(enumerator: str) -> list[str]:
    flags = CM_GETIDLIST_FILTER_ENUMERATOR | CM_GETIDLIST_FILTER_PRESENT
    size = wintypes.ULONG()
    if _cfg.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), enumerator, flags) != CR_SUCCESS or size.value < 2:
        return []
    buf = ctypes.create_unicode_buffer(size.value)
    if _cfg.CM_Get_Device_ID_ListW(enumerator, buf, size.value, flags) != CR_SUCCESS:
        return []
    return [s for s in ctypes.wstring_at(buf, size.value).split("\0") if s]


def _prop(device_id: str, key: _DEVPROPKEY):
    inst = wintypes.DWORD()
    if _cfg.CM_Locate_DevNodeW(ctypes.byref(inst), device_id, 0) != CR_SUCCESS:
        return None
    ptype, size = wintypes.ULONG(), wintypes.ULONG(512)
    buf = ctypes.create_string_buffer(512)
    if _cfg.CM_Get_DevNode_PropertyW(inst, ctypes.byref(key), ctypes.byref(ptype), buf, ctypes.byref(size), 0) != CR_SUCCESS:
        return None
    return buf.raw[: size.value]


def _text(device_id: str, key: _DEVPROPKEY):
    raw = _prop(device_id, key)
    return raw.decode("utf-16-le").rstrip("\0") if raw else None


_TOP = re.compile(r"^BTH(?:ENUM|LE)\\DEV_([0-9A-F]{12})\\", re.I)
_MAC = re.compile(r"(?:DEV_|&)([0-9A-F]{12})(?:[\\_]|$)", re.I)


def poll() -> list[Reading]:
    ids = _device_ids("BTHENUM") + _device_ids("BTHLE")
    devices = {}  # mac -> {"name", "connected", "battery"}
    for device_id in ids:
        m = _MAC.search(device_id)
        if not m:
            continue
        dev = devices.setdefault(m.group(1).upper(), {"name": None, "connected": False, "battery": None})
        if _TOP.match(device_id):
            connected = _prop(device_id, KEY_CONNECTED)
            if connected and connected[0]:
                dev["connected"] = True
            dev["name"] = dev["name"] or _text(device_id, KEY_FRIENDLY_NAME) or _text(device_id, KEY_NAME)
        battery = _prop(device_id, KEY_BATTERY)
        if battery:
            dev["battery"] = battery[0]
    return [
        Reading(f"bt:{mac}", d["name"] or mac, "Bluetooth", d["battery"], connected=d["connected"])
        for mac, d in sorted(devices.items())
        if d["battery"] is not None
    ]
