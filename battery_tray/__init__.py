from dataclasses import dataclass


@dataclass
class Reading:
    key: str                # stable id of the device on this connection type
    name: str
    kind: str               # "Bluetooth" / "Logitech"
    percent: int | None
    connected: bool = True
    charging: bool = False
    type: str = ""          # detected device type, a key of devtype.TYPES ("" = unknown)
