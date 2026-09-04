"""
ueba/features.py
================
STAGE 1 — turn raw event logs into per-entity, per-hour behaviour features.

Entities: host (agent_name) and user (user_name). Each returns one row per
entity-window with the behaviour columns UEBA scores against.

These are plain SQL aggregations over your existing tables — nothing here
connects to the collectors; it only reads what the consumer already stored.
"""
from sqlalchemy import text
import pandas as pd
# ── HOST behaviours (agent_name) : volume + diversity across all event types ──
HOST_FEATURES_SQL = """
WITH win AS (SELECT make_interval(hours => :hours) AS w)
SELECT
  e.agent_name,
  date_trunc('hour', e.timestamp)                              AS window,
  count(*)                                                     AS event_count,
  count(*) FILTER (WHERE e.src='auth'   AND e.outcome='failure') AS failed_auth,
  count(*) FILTER (WHERE e.src='auth')                          AS auth_count,
  count(*) FILTER (WHERE e.src='process')                       AS process_count,
  count(*) FILTER (WHERE e.src='network')                       AS network_count,
  count(*) FILTER (WHERE e.src='file')                          AS file_count,
  count(DISTINCT e.dst_ip)   FILTER (WHERE e.dst_ip   IS NOT NULL) AS distinct_dst_ips,
  count(DISTINCT e.dst_port) FILTER (WHERE e.dst_port IS NOT NULL) AS distinct_dst_ports,
  count(DISTINCT e.proc)     FILTER (WHERE e.proc     IS NOT NULL) AS distinct_processes,
  count(DISTINCT e.fpath)    FILTER (WHERE e.fpath    IS NOT NULL) AS distinct_files,
  coalesce(sum(e.bytes_sent),0)                                AS bytes_sent,
  min(extract(hour FROM e.timestamp))                          AS min_hour
FROM (
  SELECT agent_name, timestamp, outcome, 'auth'  AS src,
         NULL::text AS dst_ip, NULL::int AS dst_port, NULL::text AS proc,
         NULL::text AS fpath, 0::bigint AS bytes_sent
    FROM auth_events
  UNION ALL
  SELECT agent_name, timestamp, outcome, 'process',
         NULL, NULL, process_name, NULL, 0
    FROM process_events
  UNION ALL
  SELECT agent_name, timestamp, outcome, 'network',
         network_dst_ip, network_dst_port, process_name, NULL,
         coalesce(network_bytes_sent,0)
    FROM network_events
  UNION ALL
  SELECT agent_name, timestamp, outcome, 'file',
         NULL, NULL, NULL, file_path, 0
    FROM file_events
) e, win
WHERE e.timestamp >= now() - win.w
GROUP BY e.agent_name, date_trunc('hour', e.timestamp);
"""

# ── USER behaviours (user_name from auth) : the security-critical entity ────
USER_FEATURES_SQL = """
WITH win AS (SELECT make_interval(hours => :hours) AS w)
SELECT
  username,
  date_trunc('hour', timestamp)                    AS window,
  count(*)                                          AS login_count,
  count(*) FILTER (WHERE outcome='failure')          AS failed_logins,
  count(DISTINCT agent_name)                            AS distinct_hosts,
  extract(hour FROM min(timestamp))                  AS first_hour,
  extract(hour FROM max(timestamp))                  AS last_hour,
  array_agg(DISTINCT agent_name)                        AS hosts_seen
FROM auth_events, win
WHERE username IS NOT NULL AND timestamp >= now() - win.w
GROUP BY username, date_trunc('hour', timestamp);
"""

# numeric behaviours we z-score, per entity
HOST_NUMERIC = ["event_count", "failed_auth", "process_count", "network_count",
                "file_count", "distinct_dst_ips", "distinct_dst_ports",
                "distinct_processes", "distinct_files", "bytes_sent"]
USER_NUMERIC = ["login_count", "failed_logins", "distinct_hosts"]


def host_features(engine, hours=1):
    return pd.read_sql(text(HOST_FEATURES_SQL), engine, params={"hours": hours})


def user_features(engine, hours=1):
    
    return pd.read_sql(text(USER_FEATURES_SQL), engine, params={"hours": hours})
