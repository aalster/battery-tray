"""Battery of Logitech wireless devices behind a USB receiver, via HID++ 2.0."""
import logging
import time

import hid

from . import Reading

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


class _Link:
    def __init__(self, path: bytes):
        self.dev = hid.device()
        self.dev.open_path(path)

    def close(self):
        self.dev.close()

    def send(self, idx: int, feature_index: int, func: int, args: bytes = b"") -> int:
        func_sw = (func << 4) | SW_ID
        self.dev.write((bytes([LONG, idx, feature_index, func_sw]) + args).ljust(20, b"\0"))
        return func_sw

    def alive(self, indexes, timeout: float = REPLY_TIMEOUT) -> set[int]:
        """Pings all indexes at once and returns the ones that answered."""
        for idx in indexes:
            self.send(idx, 0, 0, FEATURE_UNIFIED_BATTERY.to_bytes(2, "big"))
        found, deadline = set(), time.monotonic() + timeout
        while len(found) < len(indexes):
            left = int((deadline - time.monotonic()) * 1000)
            if left <= 0:
                break
            r = self.dev.read(20, left)
            if r and len(r) >= 7 and r[1] in indexes:
                found.add(r[1])
        return found

    def request(self, idx: int, feature_index: int, func: int, args: bytes = b"", timeout: float = REPLY_TIMEOUT):
        """Returns the 16 payload bytes of the reply, or None when the device stays silent."""
        func_sw = self.send(idx, feature_index, func, args)
        deadline = time.monotonic() + timeout
        while True:
            left = int((deadline - time.monotonic()) * 1000)
            if left <= 0:
                return None
            r = self.dev.read(20, left)
            if not r:
                return None
            if len(r) < 7 or r[1] != idx:
                continue
            if r[2] == 0xFF and r[3] == feature_index and r[4] == func_sw:
                raise HidppError(f"HID++ error {r[5]}")
            if r[2] == feature_index and r[3] == func_sw:
                return bytes(r[4:20])

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
        return name.decode("ascii", "replace")


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
        paths = [d["path"] for d in hid.enumerate(VID) if d["usage_page"] == 0xFF00 and d["usage"] == 2]
        for key in [k for k in self.known if k[0] not in paths]:  # receiver unplugged
            del self.known[key]
        for path in paths:
            try:
                link = _Link(path)
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
            info = self.known[key] = {"name": name, "feature": feature, "index": fi}
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
        return Reading(f"logi:{info['name']}", info["name"], "Logitech", percent, charging=charging)
