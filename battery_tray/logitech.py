"""Battery of Logitech wireless devices behind a USB receiver, via HID++ 2.0."""
import logging
import re
import time

import hid

from . import Reading, devtype

log = logging.getLogger(__name__)

VID = 0x046D
SW_ID = 0x0A
LONG = 0x11
FEATURE_NAME = 0x0005
FEATURE_UNIFIED_BATTERY = 0x1004
FEATURE_BATTERY_STATUS = 0x1000
REPLY_TIMEOUT = 3.0  # an idle mouse can take more than a second to answer


class HidppError(Exception):
    pass


_COLLECTION = re.compile(rb"&col\d+#(.+?)&\d{4}#", re.I)


def _sibling(path: bytes):
    """The part of a HID path shared by the collections of one interface of one receiver."""
    m = _COLLECTION.search(path)
    return (path[: m.start()].lower(), m.group(1).lower()) if m else path


class _Link:
    """The long HID++ collection, plus the short one: that is where a receiver reports "nobody at this index"."""

    def __init__(self, path: bytes, short_path: bytes | None = None):
        self.devs = []
        try:
            for p in (path, short_path):
                if p:
                    dev = hid.device()
                    dev.open_path(p)
                    dev.set_nonblocking(True)
                    self.devs.append(dev)
        except OSError:
            self.close()
            raise

    def close(self):
        for dev in self.devs:
            dev.close()

    def send(self, idx: int, feature_index: int, func: int, args: bytes = b"") -> int:
        func_sw = (func << 4) | SW_ID
        self.devs[0].write((bytes([LONG, idx, feature_index, func_sw]) + args).ljust(20, b"\0"))
        return func_sw

    def alive(self, indexes) -> set[int]:
        """Pings the indexes one by one (a burst makes a Nano receiver lose replies); returns the ones that answered."""
        return {idx for idx in indexes if self.feature_index(idx, FEATURE_UNIFIED_BATTERY) is not None}

    def request(self, idx: int, feature_index: int, func: int, args: bytes = b"", timeout: float = REPLY_TIMEOUT):
        """Returns the 16 payload bytes of the reply, or None when the device stays silent or is out of reach."""
        func_sw = self.send(idx, feature_index, func, args)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            idle = True
            for dev in self.devs:
                r = dev.read(20)
                if not r:
                    continue
                idle = False
                if len(r) < 7 or r[1] != idx:
                    continue
                if r[2] == 0x8F and r[3] == feature_index and r[4] == func_sw:
                    return None  # the receiver: no such device, or it is not reachable right now
                if r[2] == 0xFF and r[3] == feature_index and r[4] == func_sw:
                    raise HidppError(f"HID++ error {r[5]}")
                if r[2] == feature_index and r[3] == func_sw:
                    return bytes(r[4:20])
            if idle:
                time.sleep(0.005)
        return None

    def feature_index(self, idx: int, feature_id: int):
        r = self.request(idx, 0, 0, feature_id.to_bytes(2, "big"))
        return None if r is None else r[0]

    def device_name(self, idx: int):
        fi = self.feature_index(idx, FEATURE_NAME)
        if not fi:
            return None
        r = self.request(idx, fi, 0)
        if r is None:
            return None
        length, name = r[0], b""
        while len(name) < length:
            chunk = self.request(idx, fi, 1, bytes([len(name)]))
            if chunk is None:
                return None
            name += chunk[: length - len(name)]
        return name.decode("ascii", "replace").strip("\0 ")  # some mice pad the name with spaces

    def device_type(self, idx: int) -> str:
        fi = self.feature_index(idx, FEATURE_NAME)
        r = self.request(idx, fi, 2) if fi else None
        return devtype.from_hidpp(r[0]) if r else ""


class LogitechSource:
    """Returns only the devices that answer right now."""

    FULL_SCAN_EVERY = 10  # polls

    def __init__(self):
        self.known = {}  # (path, idx) -> {"name", "feature", "index"}
        self.polls = 0

    def poll(self) -> list[Reading]:
        full = self.polls % self.FULL_SCAN_EVERY == 0 or not self.known
        self.polls += 1
        out = []
        vendor = [d for d in hid.enumerate(VID) if d["usage_page"] == 0xFF00]
        paths = [d["path"] for d in vendor if d["usage"] == 2]
        shorts = {_sibling(d["path"]): d["path"] for d in vendor if d["usage"] == 1}
        for key in [k for k in self.known if k[0] not in paths]:  # receiver unplugged
            del self.known[key]
        for path in paths:
            try:
                link = _Link(path, shorts.get(_sibling(path)))
            except OSError as e:
                log.warning("cannot open %s: %s", path, e)
                continue
            try:
                indexes = [i for p, i in self.known if p == path]
                if full:
                    unknown = [i for i in range(1, 7) if i not in indexes]
                    indexes += sorted(link.alive(unknown))
                for idx in indexes:
                    try:
                        reading = self._query(link, (path, idx), idx)
                    except HidppError as e:
                        log.info("device %d: %s", idx, e)
                        self.known.pop((path, idx), None)
                        continue
                    if reading:
                        out.append(reading)
            except (OSError, ValueError) as e:
                log.warning("HID I/O failed: %s", e)
            finally:
                link.close()
        return out

    def _query(self, link: _Link, key, idx: int):
        info = self.known.get(key)
        if info is None:
            for feature in (FEATURE_UNIFIED_BATTERY, FEATURE_BATTERY_STATUS):
                fi = link.feature_index(idx, feature)
                if fi is None:
                    return None  # nobody at this index
                if fi:
                    break
            else:
                return None  # device has no supported battery feature
            name = link.device_name(idx) or f"Logitech #{idx}"
            try:
                kind = link.device_type(idx)
            except HidppError:
                kind = ""
            info = self.known[key] = {"name": name, "feature": feature, "index": fi,
                                      "type": kind or devtype.from_name(name)}
            log.info("found %s (feature 0x%04X)", name, feature)
        if info["feature"] == FEATURE_UNIFIED_BATTERY:
            r = link.request(idx, info["index"], 1)
            if r is None:
                return None
            percent, charging = r[0], r[2] in (1, 2, 3)
        else:
            r = link.request(idx, info["index"], 0)
            if r is None:
                return None
            percent, charging = r[0], r[2] in (1, 2, 3, 4)
        return Reading(f"logi:{info['name']}", info["name"], "Logitech", percent, charging=charging, type=info["type"])
