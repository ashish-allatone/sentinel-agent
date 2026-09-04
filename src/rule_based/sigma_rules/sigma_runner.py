import os
from typing import Optional

from sqlalchemy import text, bindparam, String, Integer

from .sigma_compiler import load_rules
# security_alerts is the shared sink for Sigma + any hand-written detections.
ALERTS_DDL = """
CREATE TABLE IF NOT EXISTS security_alerts (
    id           BIGSERIAL PRIMARY KEY,
    rule_id      TEXT NOT NULL,
    severity     INT  NOT NULL,
    agent_name   TEXT,
    entity       TEXT,
    event_count  BIGINT,
    first_seen   TIMESTAMPTZ,
    last_seen    TIMESTAMPTZ,
    detail       TEXT,
    technique    TEXT,
    phase        TEXT,
    status       TEXT DEFAULT 'open',
    created_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (rule_id, agent_name, entity, first_seen)
);
CREATE INDEX IF NOT EXISTS ix_alerts_agent ON security_alerts (agent_name);
CREATE INDEX IF NOT EXISTS ix_alerts_entity ON security_alerts (entity);
CREATE INDEX IF NOT EXISTS ix_alerts_time ON security_alerts (last_seen);
"""

# psycopg2 used %(name)s params; SQLAlchemy text() uses :name. Convert only the
# %(name)s tokens and leave any ::type casts untouched.
import re as _re
_PARAM_RE = _re.compile(r"%\((\w+)\)s")


def _to_named(sql: str) -> str:
    return _PARAM_RE.sub(r":\1", sql)


def _default_rules_dir() -> str:
    return os.getenv("SIGMA_RULES_DIR",
                     os.path.join(os.path.dirname(__file__), "rules", "enabled"))


async def run_sigma(session, rules_path: Optional[str] = None,
                    lookback: str = "24 hours", verbose: bool = True) -> dict:
    """Run all compiled Sigma rules using an existing AsyncSession.

    Returns a summary dict. Writes findings into security_alerts.
    """
    rules_path = rules_path or _default_rules_dir()

    # ensure the sink exists
    for stmt in ALERTS_DDL.strip().split(";"):
        if stmt.strip():
            await session.execute(text(stmt))
    await session.commit()

    res = load_rules(rules_path)
    if verbose:
        print(f"[sigma] {res['loaded']} rules compiled, "
              f"{res['skipped_count']} skipped")

    findings = 0
    errors = 0
    for rule in res["rules"]:
        select_sql = _to_named(rule.to_sql(lookback))
        params = rule.query_params()
        # Type the literal SELECT params explicitly so asyncpg can infer them
        # (a bare ":severity AS severity" gives it no type context).
        stmt = text(select_sql).bindparams(
            bindparam("rule_id", type_=String),
            bindparam("severity", type_=Integer),
            bindparam("title", type_=String),
        )
        try:
            rows = (await session.execute(stmt, params)).mappings().all()
        except Exception as e:
            errors += 1
            if verbose:
                print(f"  ! {rule.title[:60]}: {str(e).splitlines()[0][:80]}")
            await session.rollback()
            continue

        technique = next((t for t in rule.tags if str(t).startswith("attack.t")), "")
        for row in rows:
            row_dict = dict(row)

            # If agent_id is missing, fetch it or set a temporary fallback string/UUID
            if "agent_id" not in row_dict:
                # Option A: Map it from a known source if you have an agent lookup table
                # Option B: Temporary mock string if your database allows strings
                row_dict["agent_id"] = "unknown_agent_id" 
            await _store(session,row_dict, technique)
            findings += 1
        if rows and verbose:
            print(f"  {rule.title[:60]}: {len(rows)}")

    await session.commit()
    return {"compiled": res["loaded"], "skipped": res["skipped_count"],
            "findings": findings, "errors": errors}


async def _store(session, row: dict, technique: str):
    row = dict(row, technique=technique, phase="sigma")
    await session.execute(text("""
        INSERT INTO security_alerts
          (rule_id, severity, agent_name, entity, event_count,
           first_seen, last_seen, detail, technique, phase)
        VALUES
          (:rule_id, :severity, :agent_name, :entity, :event_count,
           :first_seen, :last_seen, :detail, :technique, :phase)
        ON CONFLICT (rule_id, agent_name, entity, first_seen)
        DO UPDATE SET event_count = EXCLUDED.event_count,
                      last_seen   = EXCLUDED.last_seen
    """), row)


async def run_sigma_standalone(rules_path: Optional[str] = None,
                               lookback: str = "24 hours") -> dict:
    """Open its own session (for cron / CLI use outside a request)."""
    from db.db import get_async_session
    async with get_async_session() as session:
        return await run_sigma(session, rules_path, lookback)
