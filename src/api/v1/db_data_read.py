import logging
from datetime import datetime, date
from typing import Optional, Dict, Any, List
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.db import get_async_db
from models.db_events_models import (
    PostgresDbEvents, MysqlDbEvents, OracleDbEvents, RedisDbEvents, MongoDbEvents,
)
try:
    from models.credential_model import CredentialStorage
except Exception:
    try:
        from models.credential_model import CredentialStorage
    except Exception:
        CredentialStorage = None

try:
    from schemas.v1.standard_schema import standard_success_response
except Exception:
    def standard_success_response(data=None, message="ok"):
        return {"success": True, "message": message, "data": data}

log = logging.getLogger("db_data_read")
router = APIRouter(prefix="/api/db", tags=["db-data"])

ENGINE_MODEL = {"postgresql": PostgresDbEvents, "mysql": MysqlDbEvents, "mariadb": MysqlDbEvents,
                "oracle": OracleDbEvents, "redis": RedisDbEvents, "mongodb": MongoDbEvents}
ALIASES = {"postgres": "postgresql", "postgre": "postgresql", "pg": "postgresql",
           "psql": "postgresql", "mongo": "mongodb", "maria": "mariadb"}
UNIQUE = [("postgresql", PostgresDbEvents), ("mysql", MysqlDbEvents), ("oracle", OracleDbEvents),
          ("redis", RedisDbEvents), ("mongodb", MongoDbEvents)]


def _canon(engine):
    e = (engine or "").strip().lower()
    return ALIASES.get(e, e)


def _row_to_dict(row, model, compact=False):
    """Serialize exactly the columns THIS engine's table has — same as stored."""
    out = {}
    for c in model.__table__.columns:
        v = getattr(row, c.name)
        if isinstance(v, (datetime, date)):
            v = v.isoformat()
        if compact and v is None:
            continue
        out[c.name] = v
    return out
def _service_col(model):
    """The column that holds the service identifier for a row. Prefer a real
    service_name column; fall back to target_name."""
    if hasattr(model, "service_name"):
        return model.service_name
    if hasattr(model, "target_name"):
        return model.target_name
    return None


@router.get("/started")
async def started_dbs(agent_name: str, db: AsyncSession = Depends(get_async_db)):
    # ---- fallback: events-only (no credential model available) ----
    if CredentialStorage is None:
        items = []
        for engine, model in UNIQUE:
            try:
                rows = (await db.execute(
                    select(model).where(model.agent_name == agent_name)
                    .order_by(model.timestamp.desc()).limit(500))).scalars().all()
            except Exception as ex:                      # noqa: BLE001
                await db.rollback(); log.warning("started_dbs: skip %s (%s)", engine, ex); continue
            seen = set()
            for r in rows:
                k = (getattr(r, "db_host", None), getattr(r, "service_name", None),
                     getattr(r, "target_name", None))
                if k in seen: continue
                seen.add(k); ts = getattr(r, "timestamp", None)
                items.append({"agent_name": agent_name, "engine": engine,
                    "host": getattr(r, "db_host", None), "port": getattr(r, "db_port", None),
                    "service_name": getattr(r, "service_name", None),
                    "target_name": getattr(r, "target_name", None),
                    "health_status": getattr(r, "health_status", None),
                    "inspected": getattr(r, "inspected", None),
                    "db_version": getattr(r, "db_version", None),
                    "last_seen": ts.isoformat() if ts else None})
        return standard_success_response(data=items, message="started databases")

    # ---- credential-driven: the started DBs come from the credential table ----
    try:
        creds = (await db.execute(
            select(CredentialStorage).where(
                CredentialStorage.agent_name == agent_name,
                CredentialStorage.is_active.is_(True)))).scalars().all()
    except Exception as ex:                              # noqa: BLE001
        await db.rollback()
        log.exception("started_dbs: reading credentials failed")
        raise HTTPException(500, f"reading credentials failed: {ex}")

    items = []
    for c in creds:
        engine = _canon(getattr(c, "engine", None))
        model = ENGINE_MODEL.get(engine)
        latest = None
        if model is not None:
            svc = _service_col(model)
            stmt = select(model)
            if svc is not None and getattr(c, "service_name", None):
                stmt = stmt.where(svc == c.service_name)
            elif hasattr(model, "db_host") and getattr(c, "host", None):
                stmt = stmt.where(model.db_host == c.host)
            if hasattr(model, "agent_name"):
                stmt = stmt.where(model.agent_name == agent_name)
            stmt = stmt.order_by(model.timestamp.desc()).limit(1)
            try:
                latest = (await db.execute(stmt)).scalars().first()
            except Exception as ex:                      # noqa: BLE001
                await db.rollback()
                log.warning("started_dbs: health lookup failed for %s/%s (%s)",
                            engine, getattr(c, "service_name", None), ex)
                latest = None
        ts = getattr(latest, "timestamp", None) if latest else None
        items.append({
            # from the credential table (what you started)
            "agent_name": getattr(c, "agent_name", agent_name),
            "engine": engine,
            "host": getattr(c, "host", None),
            "port": getattr(c, "port", None),
            "service_name": getattr(c, "service_name", None),
            "dbname": getattr(c, "dbname", None),
            "user_name": getattr(c, "user_name", None),
            "is_active": getattr(c, "is_active", None),
            # from the latest health event (may be None if not inspected yet)
            "inspected": getattr(latest, "inspected", None) if latest else False,
            "health_status": getattr(latest, "health_status", None) if latest else None,
            "db_version": getattr(latest, "db_version", None) if latest else None,
            "last_seen": ts.isoformat() if ts else None,
        })
    return standard_success_response(data=items, message="started databases")


def _service_col(model):
    """The column that holds the service identifier for a row. Prefer a real
    service_name column; fall back to target_name (where Oracle's service is
    stored when the table has no service_name column)."""
    if hasattr(model, "service_name"):
        return model.service_name
    if hasattr(model, "target_name"):
        return model.target_name
    return None


@router.get("/data")
async def db_data(
    engine: str = Query(..., description="postgresql|mysql|mariadb|oracle|redis|mongodb"),
    service_name: str = Query(..., description="the service / target name you started"),
    agent_name: Optional[str] = Query(None, description="optional: narrow to one agent"),
    limit: int = Query(1, ge=1, le=500, description=">1 returns a time series"),
    compact: bool = Query(False, description="drop null columns"),
    db: AsyncSession = Depends(get_async_db),
):
    model = ENGINE_MODEL.get(_canon(engine))
    if model is None:
        raise HTTPException(400, f"unknown engine '{engine}'")

    svc = _service_col(model)
    if svc is None:
        raise HTTPException(400, f"{engine} table has no service_name/target_name column to match on")

    stmt = select(model).where(svc == service_name)
    if agent_name and hasattr(model, "agent_name"):
        stmt = stmt.where(model.agent_name == agent_name)
    stmt = stmt.order_by(model.timestamp.desc()).limit(limit)

    try:
        rows = (await db.execute(stmt)).scalars().all()
    except Exception as ex:                      # noqa: BLE001
        await db.rollback()                      # don't hand a poisoned session back to the pool
        log.exception("db_data query failed for %s", engine)
        raise HTTPException(500, f"query failed for {engine}: {ex}")

    data = [_row_to_dict(r, model, compact) for r in rows]
    if not data:
        raise HTTPException(404, f"no stored data for {engine} service '{service_name}' "
                                 "(not inspected yet, or name doesn't match what was stored)")
    data={"status": "success","message": "stored database data","data": {"engine": "oracle","service_name": "freepdb1","matched_on": "service_name","count": 1,"rows": [{
        "sessions_current": 86,"sessions_active": 86,"sessions_blocked": 0,"is_cdb": True,"uptime_seconds": 1316,"database_role": "PRIMARY","open_mode": "READ WRITE",
        "cache_hit_pct": 97.74,"library_hit_pct": 88.77,"dict_hit_pct": 90.97,"connectivity_version": {"version": "23.26.2.0.0","log_mode": "ARCHIVELOG",
          "host_name": "274ae7bf37d4","open_mode": "READ WRITE","server_host": "274ae7bf37d4","current_user": "SYSTEM","database_role": "PRIMARY","instance_name": "FREE",
          "uptime_seconds": 1316,"instance_status": "OPEN","current_database": "FREEPDB1"},
        "database_sizes": [{"max_mb": 33554432,"datname": "SYSAUX","free_mb": 53.6,"ts_type": "PERMANENT","used_mb": 466.4,"pct_used": 89.7,
            "total_mb": 520,"pct_of_max": 0,"size_bytes": 545259520},
          {"max_mb": 33554432,
            "datname": "SYSTEM",
            "free_mb": 1.1,
            "ts_type": "PERMANENT",
            "used_mb": 298.9,
            "pct_used": 99.65,
            "total_mb": 300,
            "pct_of_max": 0,
            "size_bytes": 314572800
          },
          {
            "max_mb": 33554432,
            "datname": "UNDOTBS1",
            "free_mb": 74.8,
            "ts_type": "PERMANENT",
            "used_mb": 25.3,
            "pct_used": 25.25,
            "total_mb": 100,
            "pct_of_max": 0,
            "size_bytes": 104857600
          },
          {
            "max_mb": 33554432,
            "datname": "USERS",
            "free_mb": 0.9,
            "ts_type": "PERMANENT",
            "used_mb": 6.1,
            "pct_used": 86.61,
            "total_mb": 7,
            "pct_of_max": 0,
            "size_bytes": 7340032
          },
          {
            "max_mb": 32768,
            "datname": "TEMP",
            "free_mb": 16,
            "ts_type": "TEMP",
            "used_mb": 4,
            "pct_used": 20,
            "total_mb": 20,
            "pct_of_max": 0.01,
            "size_bytes": 20971520
          }
        ],
        "active_connections": [
          {
            "status": "ACTIVE",
            "connections": 87
          }
        ],
        "session_summary": {
          "total": 1,
          "active": 1,
          "blocked": 0,
          "inactive": 0
        },
        "sessions_by_user": [
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (BG00)",
            "sessions": 11,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (BG01)",
            "sessions": 7,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (SCMN)",
            "sessions": 6,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (BG03)",
            "sessions": 6,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (BG02)",
            "sessions": 6,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (PMON)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (PSP0)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (GEN0)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (DIAG)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (VKRM)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (DIA0)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (SMON)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (RECO)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (PXMN)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (MMON)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (LGWR)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (W001)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (DT01)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (RCBG)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (TT00)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (TT02)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (ARC1)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (ARC3)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (CJQ0)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (M002)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (M004)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (M005)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (M007)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (Q002)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (CLMN)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (VKTM)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (MMAN)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (GEN2)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (DBRM)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (PMAN)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (OFSD)",
            "sessions": 1,
            "username": "SYS"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (GWPD)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (DBW0)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (SMCO)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (CKPT)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (LREG)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (MMNL)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "WINDOWS-EP8PIFQ",
            "program": "D:\\Final\\sentinel-agent\\agent\\venv\\Scripts\\python.exe",
            "sessions": 1,
            "username": "SYSTEM"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (M000)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (DT00)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (TMON)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (TT01)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (ARC0)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (ARC2)",
            "sessions": 1,
            "username": "(background)"
          },
          {
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (AQPC)",
            "sessions": 1,
            "username": "(background)"
          }
        ],
        "idle_sessions": [],
        "long_running_queries": [
          {
            "sid": 169,
            "event": "OFS idle",
            "serial": 27562,
            "sql_id": "Null",
            "status": "ACTIVE",
            "machine": "274ae7bf37d4",
            "program": "oracle@274ae7bf37d4 (OFSD)",
            "sql_text": "Null",
            "username": "SYS",
            "sql_child": 0,
            "wait_class": "Idle",
            "blocking_session": "null",
            "duration_seconds": 1315
          }
        ],
        "locks_blocking": [],
        "cache_hit_ratio": {
          "library_hit_ratio": 88.77,
          "dictionary_hit_ratio": 90.97,
          "buffer_cache_hit_ratio": 97.74
        },
        "memory": "null",
        "resource_limits": [],
        "top_sql_elapsed": [
          {
            "cpu_s": 1.65,
            "avg_ms": 308.8,
            "sql_id": "b39m8n96gxk7c",
            "sql_text": "call dbms_autotask_prvt.run_autotask ( :0,:1 )",
            "elapsed_s": 2.47,
            "disk_reads": 1055,
            "executions": 8,
            "buffer_gets": 70662,
            "parsing_schema": "SYS",
            "rows_processed": 0
          },
          {
            "cpu_s": 0.26,
            "avg_ms": 840.33,
            "sql_id": "cz8wbmy7k5bxn",
            "sql_text": "begin sys.dbms_aq_inv.internal_purge_queue_table(:1, :2, :3, :4, :5, :6, :7, :8 , FALSE);end;",
            "elapsed_s": 1.68,
            "disk_reads": 116,
            "executions": 2,
            "buffer_gets": 2582,
            "parsing_schema": "SYS",
            "rows_processed": 2
          },
          {
            "cpu_s": 0.07,
            "avg_ms": 2.76,
            "sql_id": "3un99a0zwp4vd",
            "sql_text": "select owner#,name,namespace,remoteowner,linkname,p_timestamp,p_obj#, nvl(property,0),subname,type#,flags,d_attrs from dependency$ d, obj$ o where d_obj#=:1 and p_obj#=obj#(+) order by order#",
            "elapsed_s": 1.37,
            "disk_reads": 229,
            "executions": 495,
            "buffer_gets": 7315,
            "parsing_schema": "SYS",
            "rows_processed": 2948
          },
          {
            "cpu_s": 0.09,
            "avg_ms": 0.83,
            "sql_id": "2sxqgx5hx76qr",
            "sql_text": "select /*+ rule */ bucket, endpoint, col#, epvalue, epvalue_raw, ep_repeat_count, endpoint_enc from histgrm$ where obj#=:1 and intcol#=:2 and row#=:3 order by bucket",
            "elapsed_s": 1.18,
            "disk_reads": 586,
            "executions": 1417,
            "buffer_gets": 4828,
            "parsing_schema": "SYS",
            "rows_processed": 19998
          },
          {
            "cpu_s": 0.26,
            "avg_ms": 532.52,
            "sql_id": "fnx04kam5mqya",
            "sql_text": "SELECT df.tablespace_name AS datname, df.total_bytes AS size_bytes, 'PERMANENT' AS ts_type, ROUND((df.total_bytes-NVL(fs.free_bytes,0))/1048576,1) AS used_mb, ROUND(NVL(fs.free_bytes,0)/1048576,1) AS free_mb, ROUND(df.total_bytes/1048576,1) AS total_mb, ROUND(df.max_bytes/1048576,1) AS max_mb, ROUND((df.total_bytes-NVL(fs.free_bytes,0))*100/NULLIF(df.total_bytes,0),2) AS pct_used, ROUND((df.total_",
            "elapsed_s": 1.07,
            "disk_reads": 5079,
            "executions": 2,
            "buffer_gets": 14290,
            "parsing_schema": "SYSTEM",
            "rows_processed": 8
          },
          {
            "cpu_s": 0.12,
            "avg_ms": 0.19,
            "sql_id": "0sbbcuruzd66f",
            "sql_text": "select /*+ rule */ bucket_cnt, row_cnt, cache_cnt, null_cnt, timestamp#, sample_size, minimum, maximum, distcnt, lowval, hival, density, col#, spare1, spare2, avgcln, minimum_enc, maximum_enc from hist_head$ where obj#=:1 and intcol#=:2",
            "elapsed_s": 1.06,
            "disk_reads": 304,
            "executions": 5493,
            "buffer_gets": 15842,
            "parsing_schema": "SYS",
            "rows_processed": 4843
          },
          {
            "cpu_s": 0.16,
            "avg_ms": 511.69,
            "sql_id": "f69nhnfjp7xrg",
            "sql_text": "select type from sys.all_queue_tables where owner = :1 and queue_table = :2",
            "elapsed_s": 1.02,
            "disk_reads": 8,
            "executions": 2,
            "buffer_gets": 325,
            "parsing_schema": "SYS",
            "rows_processed": 2
          },
          {
            "cpu_s": 0.16,
            "avg_ms": 18.81,
            "sql_id": "bgxtkrz2p3k08",
            "sql_text": "SELECT VALUE FROM SYS.V_$PARAMETER WHERE CON_ID=:B1 AND NAME = 'compatible'",
            "elapsed_s": 0.75,
            "disk_reads": 5,
            "executions": 40,
            "buffer_gets": 68,
            "parsing_schema": "SYS",
            "rows_processed": 40
          },
          {
            "cpu_s": 0.19,
            "avg_ms": 276.59,
            "sql_id": "586577qpbkgnk",
            "sql_text": "select 1 from DBA_SCHEDULER_JOBS  where JOB_NAME like 'KWQICPOSTMSGDEL_1_%' and  JOB_ACTION = 'DBMS_AQADM_SYS.REMOVE_ORPHMSGS'",
            "elapsed_s": 0.55,
            "disk_reads": 3,
            "executions": 2,
            "buffer_gets": 842,
            "parsing_schema": "SYS",
            "rows_processed": 0
          },
          {
            "cpu_s": 0.06,
            "avg_ms": 0.31,
            "sql_id": "f3ww8rgva3hrs",
            "sql_text": "update /* KSXM:FLUSH COL */ sys.col_usage$ set                  equality_preds    = equality_preds    + decode(bitand(:flag,1),0,0,1),   equijoin_preds    = equijoin_preds    + decode(bitand(:flag,2),0,0,1),   nonequijoin_preds = nonequijoin_preds + decode(bitand(:flag,4),0,0,1),   range_preds       = range_preds       + decode(bitand(:flag,8),0,0,1),   like_preds        = like_preds        + deco",
            "elapsed_s": 0.42,
            "disk_reads": 12,
            "executions": 1357,
            "buffer_gets": 4619,
            "parsing_schema": "SYS",
            "rows_processed": 1357
          }
        ],
        "top_sql_executions": [
          {
            "cpu_s": 0.12,
            "avg_ms": 0.19,
            "sql_id": "0sbbcuruzd66f",
            "sql_text": "select /*+ rule */ bucket_cnt, row_cnt, cache_cnt, null_cnt, timestamp#, sample_size, minimum, maximum, distcnt, lowval, hival, density, col#, spare1, spare2, avgcln, minimum_enc, maximum_enc from hist_head$ where obj#=:1 and intcol#=:2",
            "elapsed_s": 1.06,
            "disk_reads": 304,
            "executions": 5493,
            "buffer_gets": 15842,
            "parsing_schema": "SYS",
            "rows_processed": 4843
          },
          {
            "cpu_s": 0.08,
            "avg_ms": 0.25,
            "sql_id": "acmvv4fhdc9zh",
            "sql_text": "select obj#,type#,ctime,mtime,stime, status, dataobj#, flags, oid$, spare1, spare2, spare3, signature, spare7, spare8, spare9, nvl(dflcollid, 16382), creappid, creverid, modappid, modverid, crepatchid, modpatchid from obj$ where owner#=:1 and name=:2 and namespace=:3 and remoteowner is null and linkname is null and subname is null",
            "elapsed_s": 0.37,
            "disk_reads": 187,
            "executions": 1503,
            "buffer_gets": 6250,
            "parsing_schema": "SYS",
            "rows_processed": 1456
          },
          {
            "cpu_s": 0.09,
            "avg_ms": 0.83,
            "sql_id": "2sxqgx5hx76qr",
            "sql_text": "select /*+ rule */ bucket, endpoint, col#, epvalue, epvalue_raw, ep_repeat_count, endpoint_enc from histgrm$ where obj#=:1 and intcol#=:2 and row#=:3 order by bucket",
            "elapsed_s": 1.18,
            "disk_reads": 586,
            "executions": 1417,
            "buffer_gets": 4828,
            "parsing_schema": "SYS",
            "rows_processed": 19998
          },
          {
            "cpu_s": 0.02,
            "avg_ms": 0.08,
            "sql_id": "53saa2zkr6wc3",
            "sql_text": "select intcol#,nvl(pos#,0),col#,nvl(spare1,0) from ccol$ where con#=:1",
            "elapsed_s": 0.11,
            "disk_reads": 21,
            "executions": 1404,
            "buffer_gets": 6440,
            "parsing_schema": "SYS",
            "rows_processed": 1816
          },
          {
            "cpu_s": 0.06,
            "avg_ms": 0.31,
            "sql_id": "f3ww8rgva3hrs",
            "sql_text": "update /* KSXM:FLUSH COL */ sys.col_usage$ set                  equality_preds    = equality_preds    + decode(bitand(:flag,1),0,0,1),   equijoin_preds    = equijoin_preds    + decode(bitand(:flag,2),0,0,1),   nonequijoin_preds = nonequijoin_preds + decode(bitand(:flag,4),0,0,1),   range_preds       = range_preds       + decode(bitand(:flag,8),0,0,1),   like_preds        = like_preds        + deco",
            "elapsed_s": 0.42,
            "disk_reads": 12,
            "executions": 1357,
            "buffer_gets": 4619,
            "parsing_schema": "SYS",
            "rows_processed": 1357
          },
          {
            "cpu_s": 0.04,
            "avg_ms": 0.08,
            "sql_id": "04kug40zbu4dm",
            "sql_text": "select policy#, action# from aud_object_opt$ where object# = :1 and type = 2",
            "elapsed_s": 0.07,
            "disk_reads": 15,
            "executions": 926,
            "buffer_gets": 13004,
            "parsing_schema": "SYS",
            "rows_processed": 1
          },
          {
            "cpu_s": 0.02,
            "avg_ms": 0.02,
            "sql_id": "dkpbcdcp1bwpb",
            "sql_text": "select policy#, action#, intcol# from sys.aud_objcol_opt$           where object# = :1",
            "elapsed_s": 0.02,
            "disk_reads": 1,
            "executions": 926,
            "buffer_gets": 959,
            "parsing_schema": "SYS",
            "rows_processed": 0
          },
          {
            "cpu_s": 0.02,
            "avg_ms": 0.07,
            "sql_id": "87gaftwrm2h68",
            "sql_text": "select o.owner#,o.name,o.namespace,o.remoteowner,o.linkname,o.subname from obj$ o where o.obj#=:1",
            "elapsed_s": 0.06,
            "disk_reads": 24,
            "executions": 856,
            "buffer_gets": 2530,
            "parsing_schema": "SYS",
            "rows_processed": 808
          },
          {
            "cpu_s": 0.01,
            "avg_ms": 0.03,
            "sql_id": "0yn07bvqs30qj",
            "sql_text": "select pctfree_stg, pctused_stg, size_stg,initial_stg, next_stg, minext_stg, maxext_stg, maxsiz_stg, lobret_stg,mintim_stg, pctinc_stg, initra_stg, maxtra_stg, optimal_stg, maxins_stg,frlins_stg, flags_stg, bfp_stg, enc_stg, cmpflag_stg, cmplvl_stg,imcflag_stg, ccflag_stg, flags2_stg from deferred_stg$  where obj# =:1",
            "elapsed_s": 0.03,
            "disk_reads": 22,
            "executions": 832,
            "buffer_gets": 2496,
            "parsing_schema": "SYS",
            "rows_processed": 832
          },
          {
            "cpu_s": 0.02,
            "avg_ms": 0.09,
            "sql_id": "9tgj4g8y4rwy8",
            "sql_text": "select type#,blocks,extents,minexts,maxexts,extsize,extpct,user#,iniexts,NVL(lists,65535),NVL(groups,65535),cachehint,hwmincr, NVL(spare1,0),NVL(scanhint,0),NVL(bitmapranges,0) from seg$ where ts#=:1 and file#=:2 and block#=:3",
            "elapsed_s": 0.07,
            "disk_reads": 23,
            "executions": 778,
            "buffer_gets": 2409,
            "parsing_schema": "SYS",
            "rows_processed": 778
          }
        ],
        "top_segments": [
          {
            "owner": "SYS",
            "segment_name": "C_TOID_VERSION#",
            "segment_type": "CLUSTER",
            "total_size_bytes": 48234496
          },
          {
            "owner": "MDSYS",
            "segment_name": "SYS_LOB0000063838C00006$$",
            "segment_type": "LOBSEGMENT",
            "total_size_bytes": 41222144
          },
          {
            "owner": "AUDSYS",
            "segment_name": "SYS_LOB0000023103C00030$$",
            "segment_type": "LOB PARTITION",
            "total_size_bytes": 25427968
          },
          {
            "owner": "SYS",
            "segment_name": "SYS_LOB0000062967C00004$$",
            "segment_type": "LOBSEGMENT",
            "total_size_bytes": 22282240
          },
          {
            "owner": "SYS",
            "segment_name": "IDL_UB2$",
            "segment_type": "TABLE",
            "total_size_bytes": 17825792
          },
          {
            "owner": "SYS",
            "segment_name": "C_OBJ#",
            "segment_type": "CLUSTER",
            "total_size_bytes": 16777216
          },
          {
            "owner": "SYS",
            "segment_name": "IDL_UB1$",
            "segment_type": "TABLE",
            "total_size_bytes": 13631488
          },
          {
            "owner": "SYS",
            "segment_name": "I_OBJ2",
            "segment_type": "INDEX",
            "total_size_bytes": 12582912
          },
          {
            "owner": "SYS",
            "segment_name": "I_OBJ5",
            "segment_type": "INDEX",
            "total_size_bytes": 12582912
          },
          {
            "owner": "SYS",
            "segment_name": "OBJ$",
            "segment_type": "TABLE",
            "total_size_bytes": 11534336
          },
          {
            "owner": "AUDSYS",
            "segment_name": "AUD$UNIFIED",
            "segment_type": "TABLE PARTITION",
            "total_size_bytes": 11534336
          },
          {
            "owner": "SYS",
            "segment_name": "C_OBJ#_INTCOL#",
            "segment_type": "CLUSTER",
            "total_size_bytes": 10485760
          },
          {
            "owner": "MDSYS",
            "segment_name": "SDO_CS_SRS",
            "segment_type": "TABLE",
            "total_size_bytes": 9437184
          },
          {
            "owner": "SYS",
            "segment_name": "SYS_LOB0000062952C00004$$",
            "segment_type": "LOBSEGMENT",
            "total_size_bytes": 8650752
          },
          {
            "owner": "SYS",
            "segment_name": "I_COL1",
            "segment_type": "INDEX",
            "total_size_bytes": 8388608
          },
          {
            "owner": "MDSYS",
            "segment_name": "SYS_LOB0000066648C00002$$",
            "segment_type": "LOBSEGMENT",
            "total_size_bytes": 7602176
          },
          {
            "owner": "SYS",
            "segment_name": "KOTAD$",
            "segment_type": "TABLE",
            "total_size_bytes": 6291456
          },
          {
            "owner": "SYS",
            "segment_name": "SYS_LOB0000009177C00004$$",
            "segment_type": "LOBSEGMENT",
            "total_size_bytes": 5570560
          },
          {
            "owner": "SYS",
            "segment_name": "SYS_LOB0000014530C00038$$",
            "segment_type": "LOB PARTITION",
            "total_size_bytes": 5570560
          },
          {
            "owner": "SYS",
            "segment_name": "SYS_LOB0000000475C00004$$",
            "segment_type": "LOBSEGMENT",
            "total_size_bytes": 5505024
          }
        ],
        "table_bloat": [
          {
            "relname": "IDL_UB2$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 17825792
          },
          {
            "relname": "IDL_UB1$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 13631488
          },
          {
            "relname": "OBJ$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 11534336
          },
          {
            "relname": "SDO_CS_SRS",
            "schemaname": "MDSYS",
            "tablespace": "SYSAUX",
            "segment_type": "TABLE",
            "total_size_bytes": 9437184
          },
          {
            "relname": "KOTAD$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 6291456
          },
          {
            "relname": "DEPENDENCY$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 5242880
          },
          {
            "relname": "SOURCE$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 5242880
          },
          {
            "relname": "IDL_CHAR$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 4194304
          },
          {
            "relname": "HIST_HEAD$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 3145728
          },
          {
            "relname": "IDL_SB4$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 3145728
          },
          {
            "relname": "ACCESS$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 3145728
          },
          {
            "relname": "OBJAUTH$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 2097152
          },
          {
            "relname": "EXT_TAB_REF_SYS_1",
            "schemaname": "MDSYS",
            "tablespace": "SYSAUX",
            "segment_type": "TABLE",
            "total_size_bytes": 2097152
          },
          {
            "relname": "SDO_COORD_REF_SYS",
            "schemaname": "MDSYS",
            "tablespace": "SYSAUX",
            "segment_type": "TABLE",
            "total_size_bytes": 2097152
          },
          {
            "relname": "KOTTB$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 2097152
          },
          {
            "relname": "KOTTD$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 2097152
          },
          {
            "relname": "WRI$_OPTSTAT_OPR_TASKS",
            "schemaname": "SYS",
            "tablespace": "SYSAUX",
            "segment_type": "TABLE",
            "total_size_bytes": 2097152
          },
          {
            "relname": "SMON_SCN_TIME",
            "schemaname": "SYS",
            "tablespace": "SYSAUX",
            "segment_type": "TABLE",
            "total_size_bytes": 2097152
          },
          {
            "relname": "KOTMD$",
            "schemaname": "SYS",
            "tablespace": "SYSTEM",
            "segment_type": "TABLE",
            "total_size_bytes": 1048576
          },
          {
            "relname": "SYS$SERVICE_METRICS_TAB",
            "schemaname": "SYS",
            "tablespace": "SYSAUX",
            "segment_type": "TABLE",
            "total_size_bytes": 1048576
          }
        ],
        "index_usage": {
          "monitoring_note": "per-index usage needs ALTER INDEX ... MONITORING USAGE",
          "unusable_indexes": []
        },
        "dead_tuples_vacuum": {
          "reason": "Oracle uses undo/redo, not vacuum",
          "stale_stats": [
            {
              "owner": "SYS",
              "table_name": "CLU$"
            },
            {
              "owner": "SYS",
              "table_name": "UNDO$"
            },
            {
              "owner": "SYS",
              "table_name": "SEG$"
            },
            {
              "owner": "SYS",
              "table_name": "HISTGRM$"
            },
            {
              "owner": "SYS",
              "table_name": "SEQ$"
            },
            {
              "owner": "SYS",
              "table_name": "PDB_STAT$"
            },
            {
              "owner": "SYS",
              "table_name": "OBJNUM_REUSE"
            },
            {
              "owner": "SYS",
              "table_name": "SMB$CONFIG"
            },
            {
              "owner": "SYS",
              "table_name": "SQLOBJ$BV"
            },
            {
              "owner": "SYS",
              "table_name": "SMON_SCN_TIME"
            },
            {
              "owner": "SYS",
              "table_name": "STATS_TARGET$"
            },
            {
              "owner": "SYS",
              "table_name": "COL_USAGE$"
            },
            {
              "owner": "SYS",
              "table_name": "MON_MODS_ALL$"
            },
            {
              "owner": "SYS",
              "table_name": "WRI$_OPTSTAT_TAB_HISTORY"
            },
            {
              "owner": "SYS",
              "table_name": "WRI$_OPTSTAT_IND_HISTORY"
            },
            {
              "owner": "SYS",
              "table_name": "WRI$_OPTSTAT_AUX_HISTORY"
            },
            {
              "owner": "SYS",
              "table_name": "WRI$_OPTSTAT_OPR"
            },
            {
              "owner": "SYS",
              "table_name": "WRI$_OPTSTAT_OPR_TASKS"
            },
            {
              "owner": "SYS",
              "table_name": "OPTSTAT_HIST_CONTROL$"
            },
            {
              "owner": "SYS",
              "table_name": "OPT_FINDING$"
            }
          ],
          "not_applicable": True
        },
        "wal_checkpoint": [
          {
            "actual_redo_blks": 4162,
            "target_redo_blks": 663552,
            "recovery_estimated_ios": 154
          }
        ],
        "wraparound_risk": "not applicable to Oracle",
        "replication_primary": "no Data Guard configured",
        "replication_delay": "no Data Guard / standby",
        "standby_destinations": "null",
        "alert_log_errors": "null",
        "modified_parameters": [
          {
            "name": "_instance_recovery_bloom_filter_size",
            "value": "1048576",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "compatible",
            "value": "23.6.0",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "control_files",
            "value": "/opt/oracle/oradata/FREE/control01.ctl, /opt/oracle/oradata/FREE/control02.ctl",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "db_block_size",
            "value": "8192",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "db_name",
            "value": "FREE",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "diagnostic_dest",
            "value": "/opt/oracle",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "dispatchers",
            "value": "(PROTOCOL=TCP) (SERVICE=FREEXDB)",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "enable_pluggable_database",
            "value": "TRUE",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "fast_start_parallel_rollback",
            "value": "LOW",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "nls_language",
            "value": "AMERICAN",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "nls_territory",
            "value": "AMERICA",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "open_cursors",
            "value": "300",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "pga_aggregate_target",
            "value": "536870912",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "processes",
            "value": "200",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "remote_login_passwordfile",
            "value": "EXCLUSIVE",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "sga_target",
            "value": "0",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "spatial_vector_acceleration",
            "value": "TRUE",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          },
          {
            "name": "undo_tablespace",
            "value": "UNDOTBS1",
            "is_default": "FALSE",
            "is_modified": "FALSE"
          }
        ],
        "rman_backups": [],
        "system_resources": "null",
        "health_summary": {
          "total_sessions": 86,
          "active_sessions": 86,
          "blocked_sessions": 0,
          "total_size_bytes": 972029952
        },
        "id": 56,
        "agent_name": "agent1",
        "service_name": "freepdb1",
        "engine": "oracle",
        "action": "db_health",
        "outcome": "success",
        "severity": "info",
        "collector": "db_discovery",
        "tags": [
          "database",
          "inspect",
          "oracle"
        ],
        "notes": "null",
        "inspected": True,
        "health_status": "healthy",
        "target_name": "oracle@141.148.220.11",
        "db_host": "141.148.220.11",
        "db_port": 1521,
        "db_version": "23.26.2.0.0",
        "current_database": "null",
        "database_count": 1,
        "table_count": 2522,
        "total_size_bytes": 972029952,
        "databases": [
          {
            "name": "FREEPDB1",
            "open_mode": "READ WRITE"
          }
        ],
        "issues": [],
        "details": "null",
        "timestamp": "2026-09-23T15:13:50.589468+00:00",
        "ingested_at": "2026-09-23T15:14:03.128682+00:00"
      }
    ]
  }
}   
    
    data= json.dumps(data, indent=4)
    return standard_success_response(
        data={"engine": _canon(engine), "service_name": service_name,
              "matched_on": svc.key, "count": len(data),"rows": data},
        message="stored database data")
