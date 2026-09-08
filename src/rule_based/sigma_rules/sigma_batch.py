import os
from typing import List, Dict, Any, Optional

from sqlalchemy import text, bindparam, String, Integer

from .sigma_compiler import load_rules
from .sigma_fieldmap import LOGSOURCE_TABLE

CATEGORY_TABLE = {
    "process": "process_events",  "process_events": "process_events",
    "auth": "auth_events",        "auth_events": "auth_events",
    "network": "network_events",  "network_events": "network_events",
    "file": "file_events",        "file_events": "file_events",
    "usb": "usb_events",          "usb_events": "usb_events",
    # db engine batches each land in their own engine table:
    "mysql": "mysql_db_events",       "mysql_db_events": "mysql_db_events",
    "postgres": "postgres_db_events", "postgres_db_events": "postgres_db_events",
    "redis": "redis_db_events",       "redis_db_events": "redis_db_events",
    "oracle": "oracle_db_events",     "oracle_db_events": "oracle_db_events",
    "mongo": "mongo_db_events",       "mongo_db_events": "mongo_db_events",
}

# compile rules ONCE and keep them grouped by table (compiling 3k rules per
# batch would be far too slow). Populated lazily on first use.
_RULES_BY_TABLE: Optional[Dict[str, list]] = None


def _rules_dir() -> str:
    return os.getenv("SIGMA_RULES_DIR",
                     os.path.join(os.path.dirname(__file__), "sigma", "rules","windows"))


def _load_rules_grouped(rules_path: Optional[str] = None) -> Dict[str, list]:
    global _RULES_BY_TABLE
    if _RULES_BY_TABLE is not None:
        return _RULES_BY_TABLE
    path = rules_path or _rules_dir()
    # Fail loudly instead of silently compiling 0 rules: a wrong path is the
    # single most common cause of "compiled 0 rules". Tell the operator exactly
    # what path was tried and whether it exists / has any .yml under it.
    import glob as _glob
    if not os.path.isdir(path):
        print(f"[sigma-batch] RULES DIR NOT FOUND: {path!r}\n"
              f"              set SIGMA_RULES_DIR to your enabled/ folder "
              f"(the dir that contains the .yml rules).")
        _RULES_BY_TABLE = {}
        return _RULES_BY_TABLE
    n_yml = len(_glob.glob(os.path.join(path, "**", "*.yml"), recursive=True))
    if n_yml == 0:
        print(f"[sigma-batch] RULES DIR HAS NO .yml FILES: {path!r}\n"
              f"              point SIGMA_RULES_DIR at the folder that actually "
              f"holds the rules.")
        _RULES_BY_TABLE = {}
        return _RULES_BY_TABLE

    res = load_rules(path)
    grouped: Dict[str, list] = {}
    for rule in res["rules"]:
        # register under EVERY table the rule targets, so a batch landing in any
        # of them (e.g. any db engine table) picks the rule up.
        for t in getattr(rule, "tables", [rule.table]):
            grouped.setdefault(t, []).append(rule)
    _RULES_BY_TABLE = grouped
    print(f"[sigma-batch] rules dir: {path}")
    print(f"[sigma-batch] compiled {res['loaded']} rules "
          f"({res['skipped_count']} skipped) across {len(grouped)} tables: "
          f"{ {k: len(v) for k, v in sorted(grouped.items())} }")
    return grouped


_PARAM_RE = __import__("re").compile(r"%\((\w+)\)s")
def _to_named(sql: str) -> str:
    return _PARAM_RE.sub(r":\1", sql)


def _as_datetime(v):
    """asyncpg needs a real datetime for timestamptz params, not an ISO string.
    Accept datetime as-is; parse strings (handling a trailing 'Z')."""
    from datetime import datetime
    if v is None or isinstance(v, datetime):
        return v
    s = str(v).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        # last resort: drop sub-second / tz junk and retry
        try:
            return datetime.fromisoformat(s[:19])
        except Exception:
            return None


def _batch_bounds(rows: List[Dict[str, Any]]):
    """Min/max timestamp (as real datetimes) and the set of agent_names in this
    batch, so we can scope the Sigma SELECT to just these rows."""
    ts = [_as_datetime(r.get("timestamp")) for r in rows if r.get("timestamp")]
    ts = [t for t in ts if t is not None]
    agents = sorted({r.get("agent_name") or r.get("agent_id")
                     for r in rows if (r.get("agent_name") or r.get("agent_id")) is not None})
    return (min(ts) if ts else None, max(ts) if ts else None, agents)


async def run_sigma_on_batch(session, category: str, rows: List[Dict[str, Any]],
                             rules_path: Optional[str] = None,
                             margin_seconds: int = 2) -> dict:
    """Run the Sigma rules for this category's table, scoped to just this batch.

    session : the SAME AsyncSession that just stored the batch (rows are visible)
    category: consumer category (e.g. 'process' / 'process_events')
    rows    : the batch dicts you just inserted (need 'timestamp' and agent id)
    """
    table = CATEGORY_TABLE.get(category)
    if not table:
        return {"ran": 0, "findings": 0, "reason": f"no table for '{category}'"}

    grouped = _load_rules_grouped(rules_path)
    rules = grouped.get(table, [])
    if not rules or not rows:
        return {"ran": 0, "findings": 0, "reason": "no rules or no rows"}

    tmin, tmax, agents = _batch_bounds(rows)
    if tmin is None:
        return {"ran": 0, "findings": 0, "reason": "batch has no timestamps"}

    findings, errors = 0, 0
    first_error = None
    for rule in rules:
        # rule bounds by `now() - lookback`; for a batch we bound by the batch's
        # own time range instead. A rule may target several tables, but here we
        # only scan the ONE table this batch landed in (`table`), since that's
        # where the fresh rows are.
        scoped_sql, params = _batch_scoped_sql(rule, table, tmin, tmax, agents, margin_seconds)
        stmt = text(_to_named(scoped_sql)).bindparams(
            bindparam("rule_id", type_=String),
            bindparam("severity", type_=Integer),
            bindparam("title", type_=String),
        )
        try:
            hits = (await session.execute(stmt, params)).mappings().all()
        except Exception as e:
            errors += 1
            msg = str(e).splitlines()[0][:160]
            if first_error is None:
                first_error = f"{rule.title[:40]}: {msg}"
            # print the first few so the real cause is visible, not swallowed
            if errors <= 3:
                print(f"[sigma-batch] ERROR in '{rule.title[:40]}': {msg}")
            await session.rollback()
            continue

        technique = next((t for t in rule.tags if str(t).startswith("attack.t")), "")
        for row in hits:
            await _store_alert(session, dict(row), technique)
            findings += 1

    await session.commit()
    return {"ran": len(rules), "findings": findings, "errors": errors,
            "table": table, "batch_size": len(rows), "first_error": first_error}


def _batch_scoped_sql(rule, table, tmin, tmax, agents, margin):
    """Rebuild the rule's SELECT but time-bounded to the batch, not now()-lookback.
    `table` is the specific table this batch landed in."""
    from .sigma_fieldmap import PARENT_JOIN, TIME_COLUMN, ENTITY_COLUMN
    joins = PARENT_JOIN if rule.needs_parent_join else ""
    entity = ENTITY_COLUMN.get(table, "e.agent_name")

    params = dict(rule.params,
                  rule_id=f"SIGMA_{(rule.id or rule.title)[:40]}",
                  severity=rule.severity,
                  title=rule.title[:200],
                  tmin=tmin, tmax=tmax)

    agent_clause = ""
    if agents:
        # bind agent list so we only scan this batch's agent(s)
        names = []
        for i, a in enumerate(agents):
            k = f"agent{i}"
            params[k] = a
            names.append(f"%({k})s")
        agent_clause = f"AND e.agent_name IN ({', '.join(names)})"

    sql = f"""
SELECT %(rule_id)s AS rule_id,
       %(severity)s AS severity,
       e.agent_name,
       {entity} AS entity,
       count(*) AS event_count,
       min({TIME_COLUMN}) AS first_seen,
       max({TIME_COLUMN}) AS last_seen,
       %(title)s AS detail
FROM {table} e
{joins}
WHERE {TIME_COLUMN} >= CAST(%(tmin)s AS timestamptz) - INTERVAL '{int(margin)} seconds'
  AND {TIME_COLUMN} <= CAST(%(tmax)s AS timestamptz) + INTERVAL '{int(margin)} seconds'
  {agent_clause}
  AND ({rule.where})
GROUP BY e.agent_name, {entity}
"""
    return sql, params


async def _store_alert(session, row: dict, technique: str):
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