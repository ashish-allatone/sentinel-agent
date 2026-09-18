from typing import Dict, Any ,List
from collectors.dbprobe._util import jsonable, na, safe
from . import _thin_guard
_thin_guard.install() 
import oracledb

DRIVER = "oracledb"
def _as_list(v) -> List:
    if isinstance(v, list):
        return v
    return [] if v is None else [v] 

def inspect(params: Dict[str, Any]) -> Dict[str, Any]:

    host = params.get("host", "127.0.0.1"); port = int(params.get("port", 1521))
    if params.get("service_name"): dsn = oracledb.makedsn(host, port, service_name=params["service_name"])
    elif params.get("sid"): dsn = oracledb.makedsn(host, port, sid=params["sid"])
    else: dsn = f"{host}:{port}"
    conn = oracledb.connect(user=params.get("user"), password=params.get("password") or "", dsn=dsn)
    cur = conn.cursor()

    # tunables
    idle_thr = int(params.get("idle_threshold", params.get("idle_seconds", 300)))
    long_thr = int(params.get("long_running_threshold", 60))
    top_sql  = int(params.get("top_sql", 10))
    top_tab  = int(params.get("top_tables", 20))
    top_seg  = int(params.get("top_segments", 20))
    alert_hr = int(params.get("alert_hours", 24))

    def showq(sql):
        cur.execute(sql)
        cols = [c[0].lower() for c in cur.description]
        return [{c: jsonable(v) for c, v in zip(cols, row)} for row in cur.fetchall()]

    sec: Dict[str, Any] = {}

    # 1) connectivity + version + instance/db status (enriched)
    sec["connectivity_version"] = safe(lambda: showq(
        "SELECT (SELECT version_full FROM product_component_version WHERE product LIKE 'Oracle%' FETCH FIRST 1 ROWS ONLY) AS version, "
        "sys_context('USERENV','DB_NAME') AS current_database, USER AS current_user, "
        "sys_context('USERENV','SERVER_HOST') AS server_host, "
        "(SELECT database_role FROM v$database) AS database_role, "
        "(SELECT open_mode FROM v$database) AS open_mode, "
        "(SELECT log_mode FROM v$database) AS log_mode, "
        "(SELECT status FROM v$instance) AS instance_status, "
        "(SELECT instance_name FROM v$instance) AS instance_name, "
        "(SELECT host_name FROM v$instance) AS host_name, "
        "(SELECT ROUND((SYSDATE - startup_time)*86400) FROM v$instance) AS uptime_seconds "
        "FROM dual")[0])

    # 2) tablespace sizes + usage (permanent + temp)
    sec["database_sizes"] = safe(lambda: (showq(
        "SELECT df.tablespace_name AS datname, df.total_bytes AS size_bytes, 'PERMANENT' AS ts_type, "
        "ROUND((df.total_bytes-NVL(fs.free_bytes,0))/1048576,1) AS used_mb, "
        "ROUND(NVL(fs.free_bytes,0)/1048576,1) AS free_mb, ROUND(df.total_bytes/1048576,1) AS total_mb, "
        "ROUND(df.max_bytes/1048576,1) AS max_mb, "
        "ROUND((df.total_bytes-NVL(fs.free_bytes,0))*100/NULLIF(df.total_bytes,0),2) AS pct_used, "
        "ROUND((df.total_bytes-NVL(fs.free_bytes,0))*100/NULLIF(df.max_bytes,0),2) AS pct_of_max "
        "FROM (SELECT tablespace_name, SUM(bytes) total_bytes, "
        "SUM(DECODE(autoextensible,'YES',GREATEST(maxbytes,bytes),bytes)) max_bytes "
        "FROM dba_data_files GROUP BY tablespace_name) df "
        "LEFT JOIN (SELECT tablespace_name, SUM(bytes) free_bytes FROM dba_free_space GROUP BY tablespace_name) fs "
        "ON df.tablespace_name=fs.tablespace_name ORDER BY size_bytes DESC")
      + (safe(lambda: showq(
        "SELECT d.tablespace_name AS datname, d.total_bytes AS size_bytes, 'TEMP' AS ts_type, "
        "ROUND(NVL(u.used_bytes,0)/1048576,1) AS used_mb, "
        "ROUND((d.total_bytes-NVL(u.used_bytes,0))/1048576,1) AS free_mb, ROUND(d.total_bytes/1048576,1) AS total_mb, "
        "ROUND(d.max_bytes/1048576,1) AS max_mb, "
        "ROUND(NVL(u.used_bytes,0)*100/NULLIF(d.total_bytes,0),2) AS pct_used, "
        "ROUND(NVL(u.used_bytes,0)*100/NULLIF(d.max_bytes,0),2) AS pct_of_max "
        "FROM (SELECT tablespace_name, SUM(bytes) total_bytes, "
        "SUM(DECODE(autoextensible,'YES',GREATEST(maxbytes,bytes),bytes)) max_bytes "
        "FROM dba_temp_files GROUP BY tablespace_name) d "
        "LEFT JOIN (SELECT tablespace_name, SUM(bytes_used) used_bytes FROM v$temp_space_header GROUP BY tablespace_name) u "
        "ON d.tablespace_name=u.tablespace_name"), default=[]) or [])))

    # 3) sessions by status
    sec["active_connections"] = safe(lambda: showq(
        "SELECT status, COUNT(*) AS connections FROM v$session GROUP BY status"))

    # 4) long-running ACTIVE sessions (enriched: machine/program/wait/sql_text)
    sec["long_running_queries"] = safe(lambda: showq(
        "SELECT s.sid, s.serial# AS serial, s.username, s.machine, s.program, s.status, "
        "s.last_call_et AS duration_seconds, s.sql_id, s.sql_child_number AS sql_child, "
        "s.event, s.wait_class, s.blocking_session, "
        "(SELECT SUBSTR(sql_text,1,400) FROM v$sql q WHERE q.sql_id=s.sql_id AND q.child_number=s.sql_child_number AND ROWNUM=1) AS sql_text "
        f"FROM v$session s WHERE s.status='ACTIVE' AND s.username IS NOT NULL AND s.last_call_et >= {long_thr} "
        f"ORDER BY s.last_call_et DESC FETCH FIRST {top_sql} ROWS ONLY"))

    # 5) blocking locks (enriched)
    sec["locks_blocking"] = safe(lambda: showq(
        "SELECT sid AS blocked_sid, blocking_session AS blocking_sid, username, event, wait_class, "
        "seconds_in_wait FROM v$session WHERE blocking_session IS NOT NULL"))

    # 6) Data Guard primary stats + 7) delay
    sec["replication_primary"] = safe(lambda: showq(
        "SELECT name, value, unit FROM v$dataguard_stats") or na("no Data Guard configured"))
    sec["replication_delay"] = safe(lambda: showq(
        "SELECT name, value, unit FROM v$dataguard_stats WHERE name LIKE '%lag%'") or na("no Data Guard / standby"))

    # 8) cache hit ratios (buffer + library + dictionary)
    sec["cache_hit_ratio"] = safe(lambda: showq(
        "SELECT round((1-(phy.value/(db.value+cons.value)))*100,2) AS buffer_cache_hit_ratio, "
        "(SELECT ROUND(SUM(pinhits)/NULLIF(SUM(pins),0)*100,2) FROM v$librarycache) AS library_hit_ratio, "
        "(SELECT ROUND((1-SUM(getmisses)/NULLIF(SUM(gets),0))*100,2) FROM v$rowcache) AS dictionary_hit_ratio "
        "FROM (SELECT value FROM v$sysstat WHERE name='physical reads') phy, "
        "(SELECT value FROM v$sysstat WHERE name='db block gets') db, "
        "(SELECT value FROM v$sysstat WHERE name='consistent gets') cons")[0])

    # 9) vacuum-equivalent (undo) + stale stats
    sec["dead_tuples_vacuum"] = safe(lambda: {
        "not_applicable": True, "reason": "Oracle uses undo/redo, not vacuum",
        "stale_stats": showq("SELECT owner, table_name FROM dba_tab_statistics WHERE stale_stats='YES' FETCH FIRST 20 ROWS ONLY")})

    # 10) index usage / unusable indexes
    sec["index_usage"] = safe(lambda: {
        "monitoring_note": "per-index usage needs ALTER INDEX ... MONITORING USAGE",
        "unusable_indexes": showq("SELECT owner, index_name, status FROM dba_indexes WHERE status='UNUSABLE' FETCH FIRST 50 ROWS ONLY")})

    # 11) wraparound (N/A)
    sec["wraparound_risk"] = na("not applicable to Oracle")

    # 12) redo / checkpoint
    sec["wal_checkpoint"] = safe(lambda: showq(
        "SELECT recovery_estimated_ios, actual_redo_blks, target_redo_blks FROM v$instance_recovery"))

    # 13) largest tables ("bloat" analogue), enriched
    sec["table_bloat"] = safe(lambda: showq(
        "SELECT owner AS schemaname, segment_name AS relname, tablespace_name AS tablespace, "
        "segment_type, bytes AS total_size_bytes FROM dba_segments WHERE segment_type='TABLE' "
        f"ORDER BY bytes DESC FETCH FIRST {top_tab} ROWS ONLY"))

    # 14) host resources (set by collector via psutil)
    sec["system_resources"] = None

    sec["health_summary"] = safe(lambda: showq(
        "SELECT (SELECT COUNT(*) FROM v$session) AS total_sessions, "
        "(SELECT COUNT(*) FROM v$session WHERE status='ACTIVE') AS active_sessions, "
        "(SELECT COUNT(*) FROM v$session WHERE blocking_session IS NOT NULL) AS blocked_sessions, "
        "(SELECT SUM(bytes) FROM dba_data_files) AS total_size_bytes FROM dual")[0])

    # ---- extra Oracle datasets (new section keys; non-breaking) ----
    sec["session_summary"] = safe(lambda: showq(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN status='ACTIVE' THEN 1 ELSE 0 END) AS active, "
        "SUM(CASE WHEN status='INACTIVE' THEN 1 ELSE 0 END) AS inactive, "
        "SUM(CASE WHEN blocking_session IS NOT NULL THEN 1 ELSE 0 END) AS blocked "
        "FROM v$session WHERE type='USER'")[0])
    sec["sessions_by_user"] = safe(lambda: showq(
        "SELECT NVL(username,'(background)') AS username, NVL(program,'-') AS program, "
        "NVL(machine,'-') AS machine, COUNT(*) AS sessions FROM v$session "
        "GROUP BY username, program, machine ORDER BY sessions DESC FETCH FIRST 50 ROWS ONLY"))
    sec["idle_sessions"] = safe(lambda: showq(
        "SELECT sid, serial# AS serial, username, machine, program, status, last_call_et AS idle_seconds "
        f"FROM v$session WHERE type='USER' AND status='INACTIVE' AND last_call_et >= {idle_thr} "
        "ORDER BY last_call_et DESC FETCH FIRST 100 ROWS ONLY"))
    sec["top_sql_elapsed"] = safe(lambda: showq(
        "SELECT sql_id, SUBSTR(sql_text,1,400) AS sql_text, executions, ROUND(elapsed_time/1e6,2) AS elapsed_s, "
        "ROUND(elapsed_time/GREATEST(executions,1)/1000,2) AS avg_ms, ROUND(cpu_time/1e6,2) AS cpu_s, "
        "buffer_gets, disk_reads, rows_processed, parsing_schema_name AS parsing_schema "
        f"FROM v$sqlarea ORDER BY elapsed_time DESC FETCH FIRST {top_sql} ROWS ONLY"))
    sec["top_sql_executions"] = safe(lambda: showq(
        "SELECT sql_id, SUBSTR(sql_text,1,400) AS sql_text, executions, ROUND(elapsed_time/1e6,2) AS elapsed_s, "
        "ROUND(elapsed_time/GREATEST(executions,1)/1000,2) AS avg_ms, ROUND(cpu_time/1e6,2) AS cpu_s, "
        "buffer_gets, disk_reads, rows_processed, parsing_schema_name AS parsing_schema "
        f"FROM v$sqlarea ORDER BY executions DESC FETCH FIRST {top_sql} ROWS ONLY"))
    sec["top_segments"] = safe(lambda: showq(
        "SELECT owner, segment_name, segment_type, bytes AS total_size_bytes FROM dba_segments "
        f"ORDER BY bytes DESC FETCH FIRST {top_seg} ROWS ONLY"))
    sec["resource_limits"] = safe(lambda: showq(
        "SELECT resource_name, current_utilization, max_utilization, TRIM(limit_value) AS limit_value "
        "FROM v$resource_limit WHERE resource_name IN ('sessions','processes','transactions')"))
    sec["memory"] = safe(lambda: showq(
        "SELECT (SELECT NVL(SUM(bytes),0) FROM v$sga) AS sga_total_bytes, "
        "(SELECT bytes FROM v$sgainfo WHERE name='Buffer Cache Size') AS buffer_cache_bytes, "
        "(SELECT bytes FROM v$sgainfo WHERE name='Shared Pool Size') AS shared_pool_bytes, "
        "(SELECT value FROM v$pgastat WHERE name='total PGA allocated') AS pga_allocated_bytes, "
        "(SELECT value FROM v$pgastat WHERE name='total PGA inuse') AS pga_inuse_bytes, "
        "(SELECT value FROM v$parameter WHERE name='sga_target') AS sga_target, "
        "(SELECT value FROM v$parameter WHERE name='memory_target') AS memory_target FROM dual")[0])
    sec["modified_parameters"] = safe(lambda: showq(
        "SELECT name, value, isdefault AS is_default, ismodified AS is_modified FROM v$parameter "
        "WHERE (ismodified <> 'FALSE' OR isdefault='FALSE') AND value IS NOT NULL ORDER BY name"))
    sec["standby_destinations"] = safe(lambda: showq(
        "SELECT dest_id, destination, status, target, error FROM v$archive_dest_status "
        "WHERE destination IS NOT NULL AND status <> 'INACTIVE'") or na("no standby destinations"))
    sec["rman_backups"] = safe(lambda: showq(
        "SELECT input_type, status, start_time, end_time FROM v$rman_backup_job_details "
        "ORDER BY start_time DESC FETCH FIRST 10 ROWS ONLY"))
    sec["alert_log_errors"] = safe(lambda: showq(
        "SELECT originating_timestamp AS event_time, message_level AS level, message_text AS message "
        f"FROM v$diag_alert_ext WHERE originating_timestamp > SYSTIMESTAMP - NUMTODSINTERVAL({alert_hr},'HOUR') "
        "AND (message_text LIKE 'ORA-%' OR message_level <= 2) "
        "ORDER BY originating_timestamp DESC FETCH FIRST 100 ROWS ONLY"))

    # CDB / PDB enumeration
    is_cdb = safe(lambda: showq("SELECT cdb FROM v$database")[0].get("cdb"))
    databases = []
    if str(is_cdb).upper() == "YES":
        databases = safe(lambda: showq("SELECT name, open_mode FROM v$pdbs ORDER BY name"), default=[]) or []
        databases = [{"name": d.get("name"), "open_mode": d.get("open_mode")} for d in databases]
    if not databases:
        databases = [{"name": params.get("service_name") or params.get("sid") or "oracle"}]

    table_count = safe(lambda: showq("SELECT COUNT(*) AS c FROM dba_tables")[0].get("c"))

    cur.close(); conn.close()
    cv = sec.get("connectivity_version") or {}
    hs = sec.get("health_summary") or {}
    ss = sec.get("session_summary") or {}
    ch = sec.get("cache_hit_ratio") or {}
    metrics = {"sessions_current": hs.get("total_sessions"),
               "sessions_active": hs.get("active_sessions"),
               "sessions_blocked": hs.get("blocked_sessions") or ss.get("blocked"),
               "is_cdb": str(is_cdb).upper() == "YES",
               "uptime_seconds": cv.get("uptime_seconds"),
               "database_role": cv.get("database_role"),
               "open_mode": cv.get("open_mode"),
               "cache_hit_pct": ch.get("buffer_cache_hit_ratio"),
               "library_hit_pct": ch.get("library_hit_ratio"),
               "dict_hit_pct": ch.get("dictionary_hit_ratio")}
    points= {
        # 1 — basic_connectivity  (was sections.connectivity_version)
        "basic_connectivity": {
            "version":         cv.get("version") or sec.get("version"),
            "current_database": cv.get("current_database"),
            "current_user":    cv.get("current_user"),
            "server_host":     cv.get("server_host") or cv.get("host_name"),
            "instance_name":   cv.get("instance_name"),
            "instance_status": cv.get("instance_status"),
            "database_role":   cv.get("database_role") or metrics.get("database_role"),
            "open_mode":       cv.get("open_mode") or metrics.get("open_mode"),
            "log_mode":        cv.get("log_mode"),
            "uptime_seconds":  cv.get("uptime_seconds") or metrics.get("uptime_seconds"),
            "is_cdb":          metrics.get("is_cdb"),
            "db_port":         sec.get("db_port"),
        },
 
        # 2 — database_size  (total + per-database + tablespaces)
        "database_size": {
            "current_size_bytes": hs.get("total_size_bytes"),
            "database_count":     len(databases),
            "table_count":        table_count,
            "databases":          databases,
            "tablespaces":        sec.get("database_sizes"),
        },
 
        # 3 — active_connections  (MUST expose long_running[*].duration_seconds for _severity)
        "active_connections": {
            "by_state":     sec.get("active_connections"),
            "long_running": _as_list(sec.get("long_running_queries")),
            "summary": {
                "current": metrics.get("sessions_current"),
                "active":  metrics.get("sessions_active"),
                "blocked": metrics.get("sessions_blocked"),
            },
            "by_user": sec.get("sessions_by_user"),
            "idle":    sec.get("idle_sessions"),
            "session_summary": sec.get("session_summary"),
        },
 
        # 4 — locks_blocking
        "locks_blocking": {"blocked": _as_list(sec.get("locks_blocking"))},
 
        # 5 — replication_primary
        "replication_primary": {
            "status":               sec.get("replication_primary"),
            "standby_destinations": sec.get("standby_destinations"),
        },
 
        # 6 — replication_delay
        "replication_delay": {"status": sec.get("replication_delay")},
 
        # 7 — cache_hit_ratio
        "cache_hit_ratio": sec.get("cache_hit_ratio") or {
            "buffer_cache_hit_ratio": metrics.get("cache_hit_pct"),
            "library_hit_ratio":      metrics.get("library_hit_pct"),
            "dictionary_hit_ratio":   metrics.get("dict_hit_pct"),
        },
 
        # 8 — dead_tuples_vacuum  (Oracle: N/A + stale stats)
        "dead_tuples_vacuum": sec.get("dead_tuples_vacuum"),
 
        # 9 — index_usage
        "index_usage": sec.get("index_usage"),
 
        # 10 — transaction_wraparound  (MUST expose 'databases' list for _severity)
        "transaction_wraparound": {
            "databases": _as_list(sec.get("wraparound_databases")),  # empty on Oracle
            "note":      sec.get("wraparound_risk"),
        },
 
        # 11 — wal_checkpoint  (Oracle redo/checkpoint)
        "wal_checkpoint": {"checkpoints": _as_list(sec.get("wal_checkpoint"))},
 
        # 12 — table_bloat  (Oracle: largest segments as proxy)
        "table_bloat": {
            "tables":       _as_list(sec.get("table_bloat")),
            "top_segments": sec.get("top_segments"),
        },
 
        # 13 — system_resources  (host-level; collector sets this itself, kept for completeness)
        "system_resources": sec.get("system_resources"),
 
        # 14 — health_summary
        "health_summary": sec.get("health_summary") or {
            "total_sessions":   metrics.get("sessions_current"),
            "active_sessions":  metrics.get("sessions_active"),
            "blocked_sessions": metrics.get("sessions_blocked"),
            "total_size_bytes": hs.get("total_size_bytes"),
            "role":             metrics.get("database_role"),
            "open_mode":        metrics.get("open_mode"),
            "uptime_seconds":   metrics.get("uptime_seconds"),
        },
 
        # extras that don't map to a point field but are worth keeping under one key
        # (comment out if you don't want them stored)
        "oracle_extras": {
            "top_sql_elapsed":     sec.get("top_sql_elapsed"),
            "top_sql_executions":  sec.get("top_sql_executions"),
            "modified_parameters": sec.get("modified_parameters"),
            "resource_limits":     sec.get("resource_limits"),
            "rman_backups":        sec.get("rman_backups"),
            "alert_log_errors":    sec.get("alert_log_errors"),
        },
    }
    # return {"db_version": cv.get("version"), "current_database": cv.get("current_database"), "points": points}

    return {"version": cv.get("version"), "database_count": len(databases),
            "table_count": table_count, "total_size_bytes": hs.get("total_size_bytes"),
            "databases": databases, "metrics": metrics, "sections": sec}
