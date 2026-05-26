"""
Sentinel Agent - Universal Event Schema
Inspired by Elastic Common Schema (ECS) + OSSEC + custom security fields.
All events are normalized to this format regardless of source OS or collector.
"""

import uuid
import socket
import platform
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from enum import Enum
from config.unique_info import MACHINE_ID


# ─────────────────────────────────────────────
#  ENUMS
# ─────────────────────────────────────────────

class EventCategory(str, Enum):
    FILE        = "file"
    AUTH        = "authentication"
    NETWORK     = "network"
    PROCESS     = "process"
    SYSTEM      = "system"

class EventAction(str, Enum):
    # File
    CREATE      = "create"
    READ        = "read"
    UPDATE      = "update"   # content modified
    RENAME      = "rename"
    DELETE      = "delete"
    CHMOD       = "chmod"
    CHOWN       = "chown"
    # Auth
    LOGIN       = "login"
    LOGOUT      = "logout"
    LOGIN_FAIL  = "login_failed"
    SUDO        = "sudo"
    SSH_ACCEPT  = "ssh_accepted"
    SSH_FAIL    = "ssh_failed"
    PASSWD_CHG  = "password_change"
    USER_ADD    = "user_add"
    USER_DEL    = "user_delete"
    # Network
    CONNECT     = "connect"
    ACCEPT      = "accept"
    CLOSE       = "close"
    DNS_QUERY   = "dns_query"
    # Process
    START       = "start"
    STOP        = "stop"
    INJECT      = "inject"
    OPEN_FILE   = "open_file"

class EventOutcome(str, Enum):
    SUCCESS     = "success"
    FAILURE     = "failure"
    UNKNOWN     = "unknown"

class Severity(str, Enum):
    INFO        = "info"
    LOW         = "low"
    MEDIUM      = "medium"
    HIGH        = "high"
    CRITICAL    = "critical"


# ─────────────────────────────────────────────
#  SUB-SCHEMAS
# ─────────────────────────────────────────────

@dataclass
class HostInfo:
    hostname:       str  = field(default_factory=socket.gethostname)
    os_type:        str  = field(default_factory=lambda: platform.system().lower())   # windows | linux | darwin
    os_version:     str  = field(default_factory=platform.version)
    os_release:     str  = field(default_factory=platform.release)
    architecture:   str  = field(default_factory=platform.machine)
    ip_addresses:   List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.ip_addresses:
            try:
                self.ip_addresses = [
                    info[4][0]
                    for info in socket.getaddrinfo(socket.gethostname(), None)
                    if info[4][0] not in ('127.0.0.1', '::1')
                ]
            except Exception:
                self.ip_addresses = []


@dataclass
class FileInfo:
    path:           str  = ""
    name:           str  = ""
    extension:      str  = ""
    directory:      str  = ""
    size_bytes:     Optional[int]  = None
    sha256:         Optional[str]  = None
    sha1:           Optional[str]  = None
    md5:            Optional[str]  = None
    inode:          Optional[int]  = None
    permissions:    Optional[str]  = None   # e.g. "644" or "rwxr-xr-x"
    owner:          Optional[str]  = None
    group:          Optional[str]  = None
    created_at:     Optional[str]  = None
    modified_at:    Optional[str]  = None
    old_path:       Optional[str]  = None   # for renames
    old_sha256:     Optional[str]  = None   # hash before modification


@dataclass
class UserInfo:
    name:           Optional[str]  = None
    uid:            Optional[int]  = None
    gid:            Optional[int]  = None
    effective_uid:  Optional[int]  = None
    effective_gid:  Optional[int]  = None
    home_dir:       Optional[str]  = None
    shell:          Optional[str]  = None
    terminal:       Optional[str]  = None
    session_id:     Optional[str]  = None


@dataclass
class ProcessInfo:
    pid:            Optional[int]  = None
    ppid:           Optional[int]  = None
    name:           Optional[str]  = None
    executable:     Optional[str]  = None
    command_line:   Optional[str]  = None
    args:           List[str]      = field(default_factory=list)
    working_dir:    Optional[str]  = None
    start_time:     Optional[str]  = None
    end_time:       Optional[str]  = None
    exit_code:      Optional[int]  = None
    user:           Optional[str]  = None
    cpu_percent:    Optional[float]= None
    memory_rss_mb:  Optional[float]= None
    open_files:     List[str]      = field(default_factory=list)
    sha256:         Optional[str]  = None   # hash of the executable


@dataclass
class NetworkInfo:
    direction:          Optional[str]  = None   # inbound | outbound
    transport:          Optional[str]  = None   # tcp | udp | icmp
    protocol:           Optional[str]  = None   # http | dns | ssh | ftp ...
    src_ip:             Optional[str]  = None
    src_port:           Optional[int]  = None
    dst_ip:             Optional[str]  = None
    dst_port:           Optional[int]  = None
    bytes_sent:         Optional[int]  = None
    bytes_recv:         Optional[int]  = None
    packets_sent:       Optional[int]  = None
    packets_recv:       Optional[int]  = None
    connection_status:  Optional[str]  = None   # ESTABLISHED | LISTEN | TIME_WAIT ...
    dns_query:          Optional[str]  = None
    dns_response:       Optional[List[str]] = None
    geo_country:        Optional[str]  = None
    geo_city:           Optional[str]  = None
    is_private_ip:      Optional[bool] = None


@dataclass
class AuthInfo:
    method:         Optional[str]  = None   # password | key | token | kerberos
    source_ip:      Optional[str]  = None
    source_port:    Optional[int]  = None
    destination:    Optional[str]  = None
    failure_reason: Optional[str]  = None
    sudo_command:   Optional[str]  = None
    pam_module:     Optional[str]  = None
    session_type:   Optional[str]  = None   # ssh | tty | pts | rdp


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

# ─────────────────────────────────────────────
#  MASTER EVENT
# ─────────────────────────────────────────────

@dataclass
class SentinelEvent:
    """
    Universal normalized security event.
    Every collector outputs this exact structure.
    """
    # Core identity
    machine_id:     int = MACHINE_ID
    event_id:       str  = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:      datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ingested_at:        datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Classification
    category:       str  = EventCategory.SYSTEM
    action:         str  = ""
    outcome:        str  = EventOutcome.UNKNOWN
    severity:       str  = Severity.INFO
    tags:           List[str] = field(default_factory=list)

    # Source
    host:           HostInfo       = field(default_factory=HostInfo)
    collector:      str  = ""       # which collector generated this event
    raw_log:        Optional[str]  = None  # original unparsed line (for audit)

    # Payload (only relevant ones will be populated)
    file:           Optional[FileInfo]    = None
    user:           Optional[UserInfo]    = None
    process:        Optional[ProcessInfo] = None
    network:        Optional[NetworkInfo] = None
    auth:           Optional[AuthInfo]    = None
    usb:            Optional[USBInfo]      = None     
    harddisk:       Optional[HardDiskInfo] = None     

    # Intelligence fields (filled by analysis layer later)
    risk_score:     Optional[float]= None   # 0.0 - 100.0
    anomaly:        Optional[bool] = None
    ioc_match:      Optional[str]  = None   # matched indicator of compromise
    mitre_tactic:   Optional[str]  = None   # MITRE ATT&CK tactic
    mitre_technique:Optional[str]  = None   # MITRE ATT&CK technique ID
    notes:          Optional[str]  = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to clean dict, removing None values for compactness."""
        def clean(obj):
            if isinstance(obj, dict):
                return {k: clean(v) for k, v in obj.items() if v is not None}
            elif isinstance(obj, list):
                return [clean(i) for i in obj]
            return obj
        return clean(asdict(self))


# ─────────────────────────────────────────────
#  HASH UTILITIES
# ─────────────────────────────────────────────

def hash_file(path: str) -> Dict[str, Optional[str]]:
    """Compute SHA256, SHA1, MD5 for a file. Returns dict with all three."""
    hashes = {"sha256": None, "sha1": None, "md5": None}
    try:
        sha256 = hashlib.sha256()
        sha1   = hashlib.sha1()
        md5    = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
                sha1.update(chunk)
                md5.update(chunk)
        hashes["sha256"] = sha256.hexdigest()
        hashes["sha1"]   = sha1.hexdigest()
        hashes["md5"]    = md5.hexdigest()
    except (PermissionError, FileNotFoundError, OSError):
        pass
    return hashes


def hash_string(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


# ─────────────────────────────────────────────
#  HOST SINGLETON
# ─────────────────────────────────────────────

_HOST_INFO: Optional[HostInfo] = None

def get_host_info() -> HostInfo:
    global _HOST_INFO
    if _HOST_INFO is None:
        _HOST_INFO = HostInfo()
    return _HOST_INFO
