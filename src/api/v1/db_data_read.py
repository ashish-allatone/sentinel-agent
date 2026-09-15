import logging
from datetime import datetime, date
from typing import Optional, Dict, Any, List

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

    return standard_success_response(
        data={"engine": _canon(engine), "service_name": service_name,
              "matched_on": svc.key, "count": len(data),"latest": data[0], "rows": data},
        message="stored database data")
