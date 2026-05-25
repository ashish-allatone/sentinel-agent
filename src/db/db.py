# =============================================================
#  src/db/db.py
#  Dynamic SQLite engine + category-based bulk insert
# =============================================================
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
# from dotenv import load_dotenv

from db.base import MasterBase, Base
from models.master_model import User, AgentDBData
from models.data_log_model import MachineLogs          # kept for backward compat
from models.category_models import (
    CategoryBase,
    FileLog, NetworkLog, ProcessLog, AuthLog, SystemLog,
)

# load_dotenv()

# ── Engine registry ───────────────────────────────────────────
_engines: dict[str, AsyncEngine] = {}
db_dir_name = "databases"
os.makedirs(db_dir_name, exist_ok=True)

# ── Category → Model mapping ──────────────────────────────────
CATEGORY_MODEL_MAP = {
    "file":           FileLog,
    "network":        NetworkLog,
    "process":        ProcessLog,
    "authentication": AuthLog,
    "system":         SystemLog,
}


# ── Engine helpers ────────────────────────────────────────────
def check_db_exists(db_name: str) -> bool:
    path = db_name if db_name.endswith(".db") else f"{db_dir_name}/{db_name}.db"
    return os.path.exists(path)


def get_dynamic_engine(db_name: str) -> AsyncEngine:
    if not db_name.endswith(".db"):
        db_name = f"{db_dir_name}/{db_name}.db"
    if db_name not in _engines:
        _engines[db_name] = create_async_engine(
            f"sqlite+aiosqlite:///{db_name}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
    return _engines[db_name]


@asynccontextmanager
async def get_async_db(db_name: str):
    engine = get_dynamic_engine(db_name)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# ── Table creation ────────────────────────────────────────────
async def create_db_and_tables(db_name: str):
    """Create master tables (user, agent_db_data) in master_database."""
    print(f"Running with Database: '{db_name}'...")
    engine = get_dynamic_engine(db_name)
    async with engine.begin() as conn:
        try:
            await conn.run_sync(MasterBase.metadata.create_all)
            print(f"Successfully initialized master tables in '{db_name}'.")
        except SQLAlchemyError as e:
            raise RuntimeError(f"Failed to initialize tables: {str(e)}")


async def create_agent_db_and_tables(db_name: str):
    """Create ALL category tables inside an agent's database."""
    engine = get_dynamic_engine(db_name)
    async with engine.begin() as conn:
        try:
            # old single table (backward compat)
            await conn.run_sync(Base.metadata.create_all)
            # new category tables
            await conn.run_sync(CategoryBase.metadata.create_all)
            print(f"Created category tables in '{db_name}.db'")
        except SQLAlchemyError as e:
            raise RuntimeError(f"Failed to initialize agent tables: {str(e)}")


# ── Agent registration ────────────────────────────────────────
async def register_new_agent(meta_data: dict):
    try:
        async with get_async_db("master_database") as session:
            new_agent = AgentDBData(
                agent_name           = meta_data.get("agent_name"),
                mac_address          = meta_data.get("mac_address"),
                host_name            = meta_data.get("host_name"),
                main_ip              = meta_data.get("main_ipv4"),
                all_ips              = meta_data.get("all_ips"),
                system               = meta_data.get("system"),
                release              = meta_data.get("release"),
                version              = meta_data.get("version"),
                machine_architecture = meta_data.get("machine_architecture"),
                is_active            = True,
            )
            session.add(new_agent)
            await session.commit()
    except Exception as e:
        print(f"register_new_agent error: {e}")


# ── Category field extractors ─────────────────────────────────
def _parse_ts(raw):
    if not raw:
        return datetime.now()
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return datetime.now()


# def _base_fields(log: dict) -> dict:
#     """Fields common to every category table."""
#     return {
#         "event_id":        log.get("event_id") or str(uuid.uuid4()),
#         "machine_id":      log.get("machine_id", 0),
#         "timestamp":       _parse_ts(log.get("timestamp")),
#         "action":          log.get("action", "unknown"),
#         "outcome":         log.get("outcome", "unknown"),
#         "severity":        log.get("severity", "info"),
#         "collector":       log.get("collector"),
#         "risk_score":      log.get("risk_score", 0.0),
#         "anomaly":         log.get("anomaly", False),
#         "ioc_match":       log.get("ioc_match"),
#         "mitre_tactic":    log.get("mitre_tactic"),
#         "mitre_technique": log.get("mitre_technique"),
#         "raw_log":         log.get("raw_log"),
#         "host":            log.get("host", {}),
#     }
def _base_fields(log):
    raw = log.get('raw_log')
    return {
        'event_id':        log.get('event_id') or str(uuid.uuid4()),
        'machine_id':      log.get('machine_id', 0),
        'timestamp':       _parse_ts(log.get('timestamp')),
        'action':          log.get('action', 'unknown'),
        'outcome':         log.get('outcome', 'unknown'),
        'severity':        log.get('severity', 'info'),
        'collector':       log.get('collector'),
        'risk_score':      log.get('risk_score', 0.0),
        'anomaly':         log.get('anomaly', False),
        'ioc_match':       log.get('ioc_match'),
        'mitre_tactic':    log.get('mitre_tactic'),
        'mitre_technique': log.get('mitre_technique'),
        'raw_log':         json.dumps(raw) if isinstance(raw, (dict, list)) else raw,
        'host':            log.get('host', {}),
    }





def _build_file_record(log: dict) -> dict:
    f = log.get("file") or {}
    u = log.get("user") or {}
    return {
        **_base_fields(log),
        "file_path":        f.get("path")        or log.get("file_path"),
        "file_name":        f.get("name"),
        "file_extension":   f.get("extension"),
        "file_directory":   f.get("directory"),
        "file_size_bytes":  f.get("size_bytes"),
        "file_sha256":      f.get("sha256")      or log.get("file_sha256"),
        "file_sha1":        f.get("sha1"),
        "file_md5":         f.get("md5"),
        "file_owner":       f.get("owner"),
        "file_permissions": f.get("permissions"),
        "old_path":         f.get("old_path"),
        "old_sha256":       f.get("old_sha256"),
        "username":         u.get("name")        or log.get("username"),
    }


def _build_network_record(log: dict) -> dict:
    n = log.get("network") or {}
    p = log.get("process") or {}
    u = log.get("user")    or {}
    return {
        **_base_fields(log),
        "direction":        n.get("direction"),
        "transport":        n.get("transport"),
        "protocol":         n.get("protocol")    or log.get("net_protocol"),
        "src_ip":           n.get("src_ip")      or log.get("net_src_ip"),
        "src_port":         n.get("src_port")    or log.get("net_src_port"),
        "dst_ip":           n.get("dst_ip")      or log.get("net_dst_ip"),
        "dst_port":         n.get("dst_port")    or log.get("net_dst_port"),
        "connection_status":n.get("connection_status"),
        "bytes_sent":       n.get("bytes_sent"),
        "bytes_recv":       n.get("bytes_recv"),
        "dns_query":        n.get("dns_query"),
        "dns_response":     n.get("dns_response"),
        "geo_country":      n.get("geo_country"),
        "geo_city":         n.get("geo_city"),
        "is_private_ip":    n.get("is_private_ip"),
        "process_pid":      p.get("pid")         or log.get("process_pid"),
        "process_name":     p.get("name")        or log.get("process_name"),
        "username":         u.get("name")        or log.get("username"),
    }


def _build_process_record(log: dict) -> dict:
    p = log.get("process") or {}
    u = log.get("user")    or {}
    return {
        **_base_fields(log),
        "process_pid":    p.get("pid")          or log.get("process_pid"),
        "process_ppid":   p.get("ppid"),
        "process_name":   p.get("name")         or log.get("process_name"),
        "executable":     p.get("executable"),
        "command_line":   p.get("command_line"),
        "working_dir":    p.get("working_dir"),
        "start_time":     p.get("start_time"),
        "end_time":       p.get("end_time"),
        "exit_code":      p.get("exit_code"),
        "cpu_percent":    p.get("cpu_percent"),
        "memory_rss_mb":  p.get("memory_rss_mb"),
        "process_sha256": p.get("sha256")       or log.get("process_sha256"),
        "open_files":     p.get("open_files"),
        "username":       u.get("name")         or log.get("username"),
    }


def _build_auth_record(log: dict) -> dict:
    a = log.get("auth") or {}
    u = log.get("user") or {}
    return {
        **_base_fields(log),
        "username":       u.get("name")          or log.get("username"),
        "auth_method":    a.get("method"),
        "source_ip":      a.get("source_ip"),
        "source_port":    a.get("source_port"),
        "destination":    a.get("destination"),
        "failure_reason": a.get("failure_reason"),
        "sudo_command":   a.get("sudo_command"),
        "session_type":   a.get("session_type"),
        "pam_module":     a.get("pam_module"),
        "uid":            u.get("uid"),
        "gid":            u.get("gid"),
    }


def _build_system_record(log: dict) -> dict:
    p = log.get("process") or {}
    u = log.get("user")    or {}
    return {
        **_base_fields(log),
        "username":     u.get("name")  or log.get("username"),
        "process_name": p.get("name")  or log.get("process_name"),
        "process_pid":  p.get("pid")   or log.get("process_pid"),
        "extra_data":   {
            k: v for k, v in log.items()
            if k not in ("event_id","machine_id","timestamp","action",
                         "outcome","severity","collector","risk_score",
                         "anomaly","ioc_match","mitre_tactic",
                         "mitre_technique","raw_log","host",
                         "file","network","process","auth","user")
        },
    }


RECORD_BUILDERS = {
    "file":           _build_file_record,
    "network":        _build_network_record,
    "process":        _build_process_record,
    "authentication": _build_auth_record,
    "system":         _build_system_record,
}


# ── Main insert function ──────────────────────────────────────
async def push_data_to_db_by_category(
    agent_name: str,
    meta_data:  dict,
    category:   str,
    log_data:   list,
):
    """
    Inserts a batch of events into the correct category table
    inside the agent's dedicated SQLite database.
    """
    if not agent_name or not log_data:
        return

    # Create agent DB + all tables on first contact
    if not check_db_exists(agent_name):
        await create_agent_db_and_tables(agent_name)
        await register_new_agent(meta_data)

    model   = CATEGORY_MODEL_MAP.get(category, SystemLog)
    builder = RECORD_BUILDERS.get(category, _build_system_record)

    try:
        async with get_async_db(agent_name) as session:
            records = [builder(log) for log in log_data]
            await session.execute(insert(model), records)
            await session.commit()
            print(f"[{agent_name}] Saved {len(records)} {category} events → {model.__tablename__}")
    except Exception as e:
        print(f"[{agent_name}] DB insert error ({category}): {e}")


# ── Legacy function (keep for backward compat) ────────────────
async def push_data_to_db(data_to_push: dict):
    """Old single-table insert — kept so existing code doesn't break."""
    meta_data  = data_to_push["meta_data"]
    log_data   = data_to_push["log_data"]
    agent_name = meta_data.get("agent_name")
    if not agent_name:
        return

    if not check_db_exists(agent_name):
        await create_agent_db_and_tables(agent_name)
        await register_new_agent(meta_data)

    from models.data_log_model import MachineLogs
    try:
        async with get_async_db(agent_name) as session:
            bulk_records = []
            for log in log_data:
                bulk_records.append({
                    "machine_id": log.get("machine_id", 0),
                    "timestamp":  _parse_ts(log.get("timestamp")),
                    "category":   log.get("category", "unknown"),
                    "action":     log.get("action", "unknown"),
                    "outcome":    log.get("outcome", "unknown"),
                    "severity":   log.get("severity", "info"),
                    "tags":       log.get("tags", []),
                    "collector":  log.get("collector"),
                    # NEW

                    'raw_log': json.dumps(log.get('raw_log')) if isinstance(log.get('raw_log'), (dict, list)) else log.get('raw_log'),
                    # "raw_log":    log.get("raw_log"),
                    "host":       log.get("host", {}),
                    "file":       log.get("file"),
                    "user":       log.get("user"),
                    "process":    log.get("process"),
                    "network":    log.get("network"),
                    "auth":       log.get("auth"),
                    "file_path":      log.get("file_path"),
                    "file_sha256":    log.get("file_sha256"),
                    "process_name":   log.get("process_name"),
                    "process_pid":    log.get("process_pid"),
                    "process_sha256": log.get("process_sha256"),
                    "username":       log.get("username"),
                    "net_src_ip":     log.get("net_src_ip"),
                    "net_src_port":   log.get("net_src_port"),
                    "net_dst_ip":     log.get("net_dst_ip"),
                    "net_dst_port":   log.get("net_dst_port"),
                    "net_protocol":   log.get("net_protocol"),
                    "risk_score":     log.get("risk_score", 0.0),
                    "anomaly":        log.get("anomaly", False),
                    "ioc_match":      log.get("ioc_match"),
                    "mitre_tactic":   log.get("mitre_tactic"),
                    "mitre_technique":log.get("mitre_technique"),
                    "notes":          log.get("notes"),
                })
            await session.execute(insert(MachineLogs), bulk_records)
            await session.commit()
            print(f"Legacy insert: {len(bulk_records)} records into '{agent_name}.db'")
    except Exception as e:
        print(f"Legacy insert error: {e}")
