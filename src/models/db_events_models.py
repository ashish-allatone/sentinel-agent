from sqlalchemy import Column, Integer, BigInteger, String, Boolean, Float, TIMESTAMP,ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone

try:
    from db.base import Base          # the SAME Base db.py calls create_all on

    from models.event_model import ForceDateTime
except Exception:
    from sqlalchemy.orm import declarative_base
    Base = declarative_base()
    ForceDateTime = TIMESTAMP(timezone=True)

class DbEventCommon:
    """Columns shared by every engine table (mixin)."""
    id = Column(BigInteger, primary_key=True, autoincrement=True, index=True)
    # agent_id=Column(Integer, nullable=False, index=True)
    agent_name = Column(String, nullable=False, index=True)
    service_name = Column(String, nullable=False, index=True)
    engine = Column(String, nullable=False)
    action = Column(String, nullable=False)
    outcome = Column(String)
    severity = Column(String, index=True)
    collector = Column(String)
    tags = Column(JSONB)
    notes = Column(String)
    inspected = Column(Boolean)
    health_status = Column(String, index=True)

    target_name = Column(String, index=True)
    db_host = Column(String)
    db_port = Column(Integer)
    db_version = Column(String)
    current_database = Column(String)
    database_count = Column(Integer)
    table_count = Column(Integer)
    total_size_bytes = Column(BigInteger)
    databases = Column(JSONB)

    issues = Column(JSONB)
    details = Column(JSONB)
    timestamp = Column(ForceDateTime)
    ingested_at = Column(TIMESTAMP(timezone=True), nullable=False,
                         default=lambda: datetime.now(timezone.utc))


# --------------------------------------------------------------------------- #
class PostgresDbEvents(DbEventCommon, Base):
    __tablename__ = "postgres_db_events"
    basic_connectivity = Column(JSONB)
    database_size = Column(JSONB)
    active_connections = Column(JSONB)
    locks_blocking = Column(JSONB)
    replication_primary = Column(JSONB)
    replication_delay = Column(JSONB)
    cache_hit_ratio = Column(JSONB)
    dead_tuples_vacuum = Column(JSONB)
    index_usage = Column(JSONB)
    transaction_wraparound = Column(JSONB)
    wal_checkpoint = Column(JSONB)
    table_bloat = Column(JSONB)
    system_resources = Column(JSONB)
    health_summary = Column(JSONB)


class MysqlDbEvents(DbEventCommon, Base):
    __tablename__ = "mysql_db_events"
    basic_connectivity = Column(JSONB)
    database_size = Column(JSONB)
    active_connections = Column(JSONB)
    locks_blocking = Column(JSONB)
    replication_primary = Column(JSONB)
    replication_delay = Column(JSONB)
    cache_hit_ratio = Column(JSONB)
    dead_tuples_vacuum = Column(JSONB)
    index_usage = Column(JSONB)
    transaction_wraparound = Column(JSONB)
    wal_checkpoint = Column(JSONB)
    table_bloat = Column(JSONB)
    system_resources = Column(JSONB)
    health_summary = Column(JSONB)


class OracleDbEvents(DbEventCommon, Base):
    __tablename__ = "oracle_db_events"
    # scalar metrics
    sessions_current = Column(Integer)
    sessions_active = Column(Integer)
    sessions_blocked = Column(Integer)
    is_cdb = Column(Boolean)
    uptime_seconds = Column(BigInteger)
    database_role = Column(String)
    open_mode = Column(String)
    cache_hit_pct = Column(Float)
    library_hit_pct = Column(Float)
    dict_hit_pct = Column(Float)
    # sections
    connectivity_version = Column(JSONB)
    database_sizes = Column(JSONB)
    active_connections = Column(JSONB)
    session_summary = Column(JSONB)
    sessions_by_user = Column(JSONB)
    idle_sessions = Column(JSONB)
    long_running_queries = Column(JSONB)
    locks_blocking = Column(JSONB)
    cache_hit_ratio = Column(JSONB)
    memory = Column(JSONB)
    resource_limits = Column(JSONB)
    top_sql_elapsed = Column(JSONB)
    top_sql_executions = Column(JSONB)
    top_segments = Column(JSONB)
    table_bloat = Column(JSONB)
    index_usage = Column(JSONB)
    dead_tuples_vacuum = Column(JSONB)
    wal_checkpoint = Column(JSONB)
    wraparound_risk = Column(JSONB)
    replication_primary = Column(JSONB)
    replication_delay = Column(JSONB)
    standby_destinations = Column(JSONB)
    alert_log_errors = Column(JSONB)
    modified_parameters = Column(JSONB)
    rman_backups = Column(JSONB)
    system_resources = Column(JSONB)
    health_summary = Column(JSONB)


class RedisDbEvents(DbEventCommon, Base):
    __tablename__ = "redis_db_events"
    # scalar metrics
    uptime_seconds = Column(BigInteger)
    connected_clients = Column(Integer)
    used_memory_bytes = Column(BigInteger)
    hit_ratio_pct = Column(Float)
    role = Column(String)
    connected_slaves = Column(Integer)
    # sections
    connectivity_version = Column(JSONB)
    database_sizes = Column(JSONB)
    active_connections = Column(JSONB)
    long_running_queries = Column(JSONB)
    locks_blocking = Column(JSONB)
    replication_primary = Column(JSONB)
    replication_delay = Column(JSONB)
    cache_hit_ratio = Column(JSONB)
    dead_tuples_vacuum = Column(JSONB)
    index_usage = Column(JSONB)
    wraparound_risk = Column(JSONB)
    wal_checkpoint = Column(JSONB)
    table_bloat = Column(JSONB)
    system_resources = Column(JSONB)
    health_summary = Column(JSONB)


class MongoDbEvents(DbEventCommon, Base):
    __tablename__ = "mongo_db_events"
    # scalar metrics
    uptime_seconds = Column(BigInteger)
    connections_current = Column(Integer)
    connections_available = Column(Integer)
    connections_used_pct = Column(Float)
    resident_mb = Column(Integer)
    virtual_mb = Column(Integer)
    # sections
    connections = Column(JSONB)
    memory_mb = Column(JSONB)
    opcounters = Column(JSONB)
    network = Column(JSONB)
    largest_tables = Column(JSONB)
    connection_summary = Column(JSONB)
