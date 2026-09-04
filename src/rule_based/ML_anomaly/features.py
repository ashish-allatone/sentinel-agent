
import psycopg2.extras

# Each entry: (window_key SQL, feature SELECT). window_key groups rows; the
# SELECT must return entity, window_start, then numeric feature columns only.
#     date_trunc('hour', timestamp)  AS window_start,  replace     date_trunc('hour', timestamp)                          AS window_start,


AUTH_FEATURES = """
SELECT
    auth_source_ip                                         AS entity,
    date_trunc('hour', timestamp)                          AS window_start,
    count(*)                                               AS total_events,
    count(*) FILTER (WHERE outcome = 'failure')            AS failures,
    count(*) FILTER (WHERE outcome = 'success')            AS successes,
    count(DISTINCT username)                               AS distinct_users,
    count(DISTINCT agent_name)                             AS distinct_hosts,
    count(DISTINCT auth_session_type)                      AS distinct_session_types,
    count(*) FILTER (WHERE auth_sudo_command IS NOT NULL)  AS sudo_count,
    -- failure ratio: brute force pushes this toward 1
    (count(*) FILTER (WHERE outcome = 'failure'))::float
        / GREATEST(count(*), 1)                            AS failure_ratio,
    -- night activity (20:00-06:00) is unusual for most source IPs
    count(*) FILTER (WHERE EXTRACT(HOUR FROM timestamp) >= 20
                        OR EXTRACT(HOUR FROM timestamp) < 6) AS night_events,
    EXTRACT(HOUR FROM date_trunc('hour', timestamp))       AS hour_of_day
FROM auth_events
WHERE timestamp > now() - INTERVAL '{lookback}'
  AND auth_source_ip IS NOT NULL
GROUP BY auth_source_ip, date_trunc('hour', timestamp)
"""

NETWORK_FEATURES = """
SELECT
    agent_name                                            AS entity,
    date_trunc('hour', timestamp)                         AS window_start,
    count(*)                                              AS total_events,
    count(DISTINCT network_dst_ip)                        AS distinct_dst_ips,
    count(DISTINCT network_dst_port)                      AS distinct_dst_ports,
    count(*) FILTER (WHERE network_is_private_ip IS FALSE) AS external_conns,
    COALESCE(sum(network_bytes_sent), 0)                  AS bytes_sent,
    COALESCE(sum(network_bytes_recv), 0)                  AS bytes_recv,
    -- outbound-to-external byte volume is the exfil signal
    COALESCE(sum(network_bytes_sent) FILTER (
        WHERE network_direction = 'outbound'
          AND network_is_private_ip IS FALSE), 0)         AS external_bytes_sent,
    count(DISTINCT network_dns_query)                     AS distinct_dns,
    count(*) FILTER (WHERE length(COALESCE(network_dns_query,'')) > 50) AS long_dns,
    EXTRACT(HOUR FROM date_trunc('hour', timestamp) )      AS hour_of_day
FROM network_events
WHERE timestamp > now() - INTERVAL '{lookback}'
GROUP BY agent_name, date_trunc('hour', timestamp) 
"""

PROCESS_FEATURES = """
SELECT
    agent_name                                            AS entity,
    date_trunc('hour', timestamp)              AS window_start,
    count(*)                                              AS total_events,
    count(DISTINCT process_name)                          AS distinct_processes,
    count(DISTINCT process_user)                          AS distinct_users,
    count(DISTINCT process_sha256)                        AS distinct_hashes,
    -- suspicious command-line shape (encoded / LOLBins / download-and-run)
    count(*) FILTER (WHERE process_command_line ILIKE '%-enc %'
                        OR process_command_line ILIKE '%encodedcommand%'
                        OR process_command_line ILIKE '%certutil%'
                        OR process_command_line ILIKE '%FromBase64String%'
                        OR process_command_line ILIKE '%Invoke-Expression%'
                        OR process_command_line ILIKE '%vssadmin%delete%') AS suspicious_cmds,
    avg(COALESCE(process_cpu_percent, 0))                 AS avg_cpu,
    avg(COALESCE(process_memory_rss_mb, 0))               AS avg_mem,
    EXTRACT(HOUR FROM date_trunc('hour', timestamp) )      AS hour_of_day
FROM process_events
WHERE timestamp > now() - INTERVAL '{lookback}'
GROUP BY agent_name, date_trunc('hour', timestamp) 
"""

FILE_FEATURES = """
SELECT
    agent_name                                           AS entity,
    date_trunc('hour', timestamp)                          AS window_start,
    count(*)                                              AS total_events,
    count(*) FILTER (WHERE action IN ('update','delete','rename')) AS modifications,
    count(*) FILTER (WHERE action = 'delete')             AS deletions,
    count(DISTINCT file_extension)                        AS distinct_exts,
    count(DISTINCT user_name)                             AS distinct_users,
    count(DISTINCT file_directory)                        AS distinct_dirs,
    -- known ransomware extensions written this hour
    count(*) FILTER (WHERE lower(file_extension) IN
        ('.locked','.encrypted','.crypt','.enc','.wncry','.conti','.ryk')) AS ransom_ext,
    COALESCE(sum(file_size_bytes), 0)                     AS total_bytes,
    EXTRACT(HOUR FROM date_trunc('hour', timestamp) )      AS hour_of_day
FROM file_events
WHERE timestamp > now() - INTERVAL '{lookback}'
GROUP BY agent_name, date_trunc('hour', timestamp) 
"""

# table -> (source table for write-back, entity column, feature SQL, feature names)
FEATURE_SETS = {
    "auth_events": {
        "entity_col": "auth_source_ip",
        "sql": AUTH_FEATURES,
        "features": ["total_events", "failures", "successes", "distinct_users",
                     "distinct_hosts", "distinct_session_types", "sudo_count",
                     "failure_ratio", "night_events", "hour_of_day"],
    },
    "network_events": {
        "entity_col": "agent_name",
        "sql": NETWORK_FEATURES,
        "features": ["total_events", "distinct_dst_ips", "distinct_dst_ports",
                     "external_conns", "bytes_sent", "bytes_recv",
                     "external_bytes_sent", "distinct_dns", "long_dns", "hour_of_day"],
    },
    "process_events": {
        "entity_col": "agent_name",
        "sql": PROCESS_FEATURES,
        "features": ["total_events", "distinct_processes", "distinct_users",
                     "distinct_hashes", "suspicious_cmds", "avg_cpu", "avg_mem",
                     "hour_of_day"],
    },
    "file_events": {
        "entity_col": "agent_name",
        "sql": FILE_FEATURES,
        "features": ["total_events", "modifications", "deletions", "distinct_exts",
                     "distinct_users", "distinct_dirs", "ransom_ext", "total_bytes",
                     "hour_of_day"],
    },
}


def load_features(conn, table, lookback="30 days"):
    """Return (rows, feature_names). rows are dicts with entity/window_start/features."""
    spec = FEATURE_SETS[table]
    # print(lookback)
    sql = spec["sql"].format(lookback=lookback)
    # print(sql)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
        print(len(rows))
    return rows, spec["features"]
