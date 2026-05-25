# =============================================================
#  src/models/category_models.py
#  Separate SQLAlchemy models for each event category
#  Tables: file_logs, network_logs, process_logs,
#          auth_logs, system_logs
# =============================================================

import uuid
import json
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean,
    DateTime, Float, TypeDecorator, func
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase


# ── Shared Base (same pattern as existing Base) ────────────
class CategoryBase(AsyncAttrs, DeclarativeBase):
    pass


# ── SQLite JSON helper (same as existing code) ─────────────
class SQLiteJSON(TypeDecorator):
    impl = Text
    def process_bind_param(self, value, dialect):
        if value is not None:
            return json.dumps(value)
        return None
    def process_result_value(self, value, dialect):
        if value is not None:
            try:
                return json.loads(value)
            except Exception:
                return value
        return None


# ─────────────────────────────────────────────────────────────
# 1. FILE LOGS
# ─────────────────────────────────────────────────────────────
class FileLog(CategoryBase):
    __tablename__ = "file_logs"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    event_id        = Column(String(36), default=lambda: str(uuid.uuid4()), unique=True)
    machine_id      = Column(Integer, nullable=False, index=True)
    timestamp       = Column(DateTime, nullable=False, default=func.now())
    ingested_at     = Column(DateTime, nullable=False, server_default=func.now())

    # Classification
    action          = Column(String(64),  nullable=False)   # create|read|update|rename|delete|chmod
    outcome         = Column(String(32),  nullable=False)
    severity        = Column(String(16),  nullable=False, index=True)
    collector       = Column(String(128), nullable=True)

    # File specifics
    file_path       = Column(String,      nullable=True, index=True)
    file_name       = Column(String(256), nullable=True)
    file_extension  = Column(String(32),  nullable=True)
    file_directory  = Column(String,      nullable=True)
    file_size_bytes = Column(Integer,     nullable=True)
    file_sha256     = Column(String(64),  nullable=True, index=True)
    file_sha1       = Column(String(40),  nullable=True)
    file_md5        = Column(String(32),  nullable=True)
    file_owner      = Column(String(128), nullable=True)
    file_permissions= Column(String(16),  nullable=True)
    old_path        = Column(String,      nullable=True)   # for renames
    old_sha256      = Column(String(64),  nullable=True)   # hash before modify

    # User who triggered the event
    username        = Column(String(128), nullable=True, index=True)

    # Intelligence
    risk_score      = Column(Float,   default=0.0)
    anomaly         = Column(Boolean, default=False)
    ioc_match       = Column(String,  nullable=True)
    mitre_tactic    = Column(String(128), nullable=True)
    mitre_technique = Column(String(64),  nullable=True)

    raw_log         = Column(Text, nullable=True)
    host            = Column(SQLiteJSON, nullable=True)


# ─────────────────────────────────────────────────────────────
# 2. NETWORK LOGS
# ─────────────────────────────────────────────────────────────
class NetworkLog(CategoryBase):
    __tablename__ = "network_logs"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    event_id        = Column(String(36), default=lambda: str(uuid.uuid4()), unique=True)
    machine_id      = Column(Integer, nullable=False, index=True)
    timestamp       = Column(DateTime, nullable=False, default=func.now())
    ingested_at     = Column(DateTime, nullable=False, server_default=func.now())

    action          = Column(String(64),  nullable=False)   # connect|accept|close|dns_query
    outcome         = Column(String(32),  nullable=False)
    severity        = Column(String(16),  nullable=False, index=True)
    collector       = Column(String(128), nullable=True)

    # Network specifics
    direction       = Column(String(16),  nullable=True)    # inbound|outbound
    transport       = Column(String(16),  nullable=True)    # tcp|udp|icmp
    protocol        = Column(String(32),  nullable=True)    # http|dns|ssh|ftp
    src_ip          = Column(String(64),  nullable=True, index=True)
    src_port        = Column(Integer,     nullable=True)
    dst_ip          = Column(String(64),  nullable=True, index=True)
    dst_port        = Column(Integer,     nullable=True)
    connection_status = Column(String(32),nullable=True)    # ESTABLISHED|TIME_WAIT
    bytes_sent      = Column(Integer,     nullable=True)
    bytes_recv      = Column(Integer,     nullable=True)
    dns_query       = Column(String(256), nullable=True)
    dns_response    = Column(SQLiteJSON,  nullable=True)
    geo_country     = Column(String(64),  nullable=True)
    geo_city        = Column(String(64),  nullable=True)
    is_private_ip   = Column(Boolean,     nullable=True)

    # Process that opened the connection
    process_pid     = Column(Integer,     nullable=True)
    process_name    = Column(String(256), nullable=True)
    username        = Column(String(128), nullable=True)

    # Intelligence
    risk_score      = Column(Float,   default=0.0)
    anomaly         = Column(Boolean, default=False)
    ioc_match       = Column(String,  nullable=True)
    mitre_tactic    = Column(String(128), nullable=True)
    mitre_technique = Column(String(64),  nullable=True)

    raw_log         = Column(Text, nullable=True)
    host            = Column(SQLiteJSON, nullable=True)


# ─────────────────────────────────────────────────────────────
# 3. PROCESS LOGS
# ─────────────────────────────────────────────────────────────
class ProcessLog(CategoryBase):
    __tablename__ = "process_logs"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    event_id        = Column(String(36), default=lambda: str(uuid.uuid4()), unique=True)
    machine_id      = Column(Integer, nullable=False, index=True)
    timestamp       = Column(DateTime, nullable=False, default=func.now())
    ingested_at     = Column(DateTime, nullable=False, server_default=func.now())

    action          = Column(String(64),  nullable=False)   # start|stop|inject|open_file
    outcome         = Column(String(32),  nullable=False)
    severity        = Column(String(16),  nullable=False, index=True)
    collector       = Column(String(128), nullable=True)

    # Process specifics
    process_pid     = Column(Integer,     nullable=True, index=True)
    process_ppid    = Column(Integer,     nullable=True)
    process_name    = Column(String(256), nullable=True, index=True)
    executable      = Column(String,      nullable=True)
    command_line    = Column(Text,        nullable=True)
    working_dir     = Column(String,      nullable=True)
    start_time      = Column(String,      nullable=True)
    end_time        = Column(String,      nullable=True)
    exit_code       = Column(Integer,     nullable=True)
    cpu_percent     = Column(Float,       nullable=True)
    memory_rss_mb   = Column(Float,       nullable=True)
    process_sha256  = Column(String(64),  nullable=True)
    open_files      = Column(SQLiteJSON,  nullable=True)

    username        = Column(String(128), nullable=True, index=True)

    # Intelligence
    risk_score      = Column(Float,   default=0.0)
    anomaly         = Column(Boolean, default=False)
    ioc_match       = Column(String,  nullable=True)
    mitre_tactic    = Column(String(128), nullable=True)
    mitre_technique = Column(String(64),  nullable=True)

    raw_log         = Column(Text, nullable=True)
    host            = Column(SQLiteJSON, nullable=True)


# ─────────────────────────────────────────────────────────────
# 4. AUTH LOGS
# ─────────────────────────────────────────────────────────────
class AuthLog(CategoryBase):
    __tablename__ = "auth_logs"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    event_id        = Column(String(36), default=lambda: str(uuid.uuid4()), unique=True)
    machine_id      = Column(Integer, nullable=False, index=True)
    timestamp       = Column(DateTime, nullable=False, default=func.now())
    ingested_at     = Column(DateTime, nullable=False, server_default=func.now())

    action          = Column(String(64),  nullable=False)   # login|logout|login_failed|sudo|ssh_accepted
    outcome         = Column(String(32),  nullable=False)
    severity        = Column(String(16),  nullable=False, index=True)
    collector       = Column(String(128), nullable=True)

    # Auth specifics
    username        = Column(String(128), nullable=True, index=True)
    auth_method     = Column(String(64),  nullable=True)    # password|key|token|kerberos
    source_ip       = Column(String(64),  nullable=True, index=True)
    source_port     = Column(Integer,     nullable=True)
    destination     = Column(String(128), nullable=True)
    failure_reason  = Column(String(256), nullable=True)
    sudo_command    = Column(Text,        nullable=True)
    session_type    = Column(String(32),  nullable=True)    # ssh|tty|rdp
    pam_module      = Column(String(128), nullable=True)

    # User context
    uid             = Column(Integer,     nullable=True)
    gid             = Column(Integer,     nullable=True)

    # Intelligence
    risk_score      = Column(Float,   default=0.0)
    anomaly         = Column(Boolean, default=False)
    ioc_match       = Column(String,  nullable=True)
    mitre_tactic    = Column(String(128), nullable=True)
    mitre_technique = Column(String(64),  nullable=True)

    raw_log         = Column(Text, nullable=True)
    host            = Column(SQLiteJSON, nullable=True)


# ─────────────────────────────────────────────────────────────
# 5. SYSTEM LOGS  (USB, HardDisk, generic system events)
# ─────────────────────────────────────────────────────────────
class SystemLog(CategoryBase):
    __tablename__ = "system_logs"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    event_id        = Column(String(36), default=lambda: str(uuid.uuid4()), unique=True)
    machine_id      = Column(Integer, nullable=False, index=True)
    timestamp       = Column(DateTime, nullable=False, default=func.now())
    ingested_at     = Column(DateTime, nullable=False, server_default=func.now())

    action          = Column(String(64),  nullable=False)
    outcome         = Column(String(32),  nullable=False)
    severity        = Column(String(16),  nullable=False, index=True)
    collector       = Column(String(128), nullable=True)

    # Generic system fields
    username        = Column(String(128), nullable=True)
    process_name    = Column(String(256), nullable=True)
    process_pid     = Column(Integer,     nullable=True)

    # Raw payload for flexibility
    raw_log         = Column(Text,        nullable=True)
    extra_data      = Column(SQLiteJSON,  nullable=True)   # any extra fields

    # Intelligence
    risk_score      = Column(Float,   default=0.0)
    anomaly         = Column(Boolean, default=False)
    ioc_match       = Column(String,  nullable=True)
    mitre_tactic    = Column(String(128), nullable=True)
    mitre_technique = Column(String(64),  nullable=True)

    host            = Column(SQLiteJSON, nullable=True)
