# agent/schema/usb_schema.py
from dataclasses import dataclass
from typing import Optional
from enum import Enum

class USBEventAction(str, Enum):
    CONNECTED      = "usb_connected"
    DISCONNECTED   = "usb_disconnected"
    RAW_DEVICE     = "usb_raw_device"
    AUTORUN_FOUND  = "usb_autorun_found"
    DATA_TRANSFER  = "usb_data_transfer"

@dataclass
class USBInfo:
    device:      Optional[str] = None   # /dev/sdb or D:\
    mountpoint:  Optional[str] = None
    fstype:      Optional[str] = None
    label:       Optional[str] = None
    vendor:      Optional[str] = None
    model:       Optional[str] = None
    serial:      Optional[str] = None
    size_bytes:  Optional[int] = None
    used_bytes:  Optional[int] = None
    autorun_files: Optional[list] = None  # files found on connect scan