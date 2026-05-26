from dataclasses import dataclass
from typing import Optional
from enum import Enum

class HardDiskEventAction(str, Enum):
    SPACE_WARNING    = "disk_space_warning"
    SPACE_CRITICAL   = "disk_space_critical"
    PARTITION_NEW    = "disk_partition_new"
    PARTITION_REMOVED= "disk_partition_removed"
    SMART_FAILURE    = "disk_smart_failure"
    RAPID_FREE       = "disk_rapid_free_increase"
    MOUNT_OPTS_CHANGED = "disk_mount_opts_changed"

@dataclass
class HardDiskInfo:
    device:      Optional[str]   = None
    mountpoint:  Optional[str]   = None
    fstype:      Optional[str]   = None
    opts:        Optional[str]   = None
    total_bytes: Optional[int]   = None
    used_bytes:  Optional[int]   = None
    free_bytes:  Optional[int]   = None
    percent:     Optional[float] = None
    smart_alerts: Optional[list] = None   # list of SMART failure strings