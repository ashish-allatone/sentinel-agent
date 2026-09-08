import os
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv
from sqlalchemy import insert
from contextlib import asynccontextmanager
from db.base import Base
from models.user_model import Users , CommunicationChannel
from models.agent_model import AgentGroups , Agents
from models.event_model import AuthEvents , ProcessEvents , NetworkEvents , USBEvents , FileEvents
from models.db_events_models import (
    PostgresDbEvents, MysqlDbEvents, OracleDbEvents, RedisDbEvents, MongoDbEvents,
)
from models.agent_model import AgentGroups , Agents , ServicesCredentials
from models.fly_events_model import FlyEvents
from models.web_server_events_model import WebServerEvents
from models.event_model import AuthEvents , ProcessEvents , NetworkEvents , USBEvents , FileEvents , CapacityMonitoringEvents
import json
from datetime import datetime
from models.appserver_events_model import AppServerEvents
load_dotenv()
import hashlib, json
from rule_based.sigma_rules.sigma_batch import run_sigma_on_batch


dbuser = "dbmasteruser"
dbpassword = "dbmasterpassword"
dbendpoint = "sentinelpg"
dbname = "testdb"


DATABASE_URL_ASYNC=f"postgresql+asyncpg://{dbuser}:{dbpassword}@{dbendpoint}:5432/{dbname}"

async_engine: AsyncEngine = create_async_engine(DATABASE_URL_ASYNC)

AsyncSessionLocal = sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False
)




async def get_async_db():
    """
    Async context manager providing a scoped AsyncSession for a designated target database.

    Examples:
    ---------
    >>> async with get_async_db() as session:
    >>>     result = await session.execute(select(User))
    >>>     data = result.scalars().all()
    """

    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

get_async_session = asynccontextmanager(get_async_db)

async def create_db_and_tables():
   
    print(f"Running with Database...")
    
    
    async with async_engine.begin() as conn:
        try:        
            await conn.run_sync(Base.metadata.create_all)
        except SQLAlchemyError as e:
            raise RuntimeError(message = f"Failed to initialize tables with data: {str(e)}")
        

CATEGORIES_TABLE_MAPPING = {
    "authentication": AuthEvents ,
    "file": FileEvents,
    "network": NetworkEvents,
    "process": ProcessEvents ,
    "usb" : USBEvents,
    "postgres_health": PostgresDbEvents,
    "mysql_health":    MysqlDbEvents,
    "oracle_health":   OracleDbEvents,
    "redis_health":    RedisDbEvents,
    "mongodb_health":  MongoDbEvents,
    "web_server_health": WebServerEvents,
    "fly_health" : FlyEvents,
    "resource" : CapacityMonitoringEvents,
    "appserver_health":AppServerEvents,
    }
# # per table: which columns define "the same log" (exclude id, timestamp, msg_id, pid, counts)
# FINGERPRINT_FIELDS = {
#     "file":    ["agent_name","action","file_path","file_name","file_extension","user_name"],
#     "process": ["agent_name","process_name","process_command_line","","proces_executable","process_user"],
#     "network": ["agent_name","network_dst_ip","network_dst_port","network_transport","process_command_line","process_name"],
#     "auth":    ["agent_name","action","username","outcome"],
#     "usb":     ["agent_name","action","usb_serial","usb_product"],
# }

# def fingerprint(category, record):
#     fields = FINGERPRINT_FIELDS.get(category, [])
#     vals = {k: record.get(k) for k in fields}
#     blob = json.dumps(vals, sort_keys=True, default=str)
#     return hashlib.sha256(blob.encode()).hexdigest()


# # lives on the consumer, survives across batches
# LAST_FP = {}   # {(agent_id, category): last_fingerprint}

# def drop_adjacent_dupes(category, records):
#     out = []
#     for rec in records:                       # records already in arrival order
#         fp = fingerprint(category, rec)
#         key =(rec.get("agent_name"), category)
#         if LAST_FP.get(key) == fp:
#             continue                          # same as previous -> skip
#         LAST_FP[key] = fp                     # different -> keep, remember it
#         rec["event_fingerprint"] = fp
#         out.append(rec)
#     return out

        
async def push_data_to_db(data_to_push):
    meta_data = data_to_push.get("meta_data")
    events_data = data_to_push.get("event_data")
    agent_name = meta_data.get("agent_name")
    category_wise_data = {}
    available_categories = []
    for ed in events_data:
        cat = ed.get("category")
        if cat:
            ed["agent_name"] = agent_name
            if not category_wise_data.get(cat):
                available_categories.append(cat)
                category_wise_data[cat] = []
                if isinstance(ed.get("tags"), str):
                    try:
                        ed["tags"] = json.loads(ed["tags"])
                    except Exception:
                        ed["tags"] = [ed["tags"]] # Fallback array
            
            category_wise_data[cat].append(ed)
    try:
        for cat, records in category_wise_data.items():
                # records = drop_adjacent_dupes(cat, records)
                # if not records:
                #     continue
                model_class = CATEGORIES_TABLE_MAPPING.get(cat)
                if not model_class:
                    return
                
                # 1. Get valid column names for this specific SQLAlchemy model
                valid_columns = set(model_class.__table__.columns.keys())
                # 2. Filter out any extra keys from the incoming dictionaries
                cleaned_records = [
                    {k: v for k, v in record.items() if k in valid_columns}
                    for record in records
                ]
                
                # 3. Execute bulk insert if there are valid records to push
                if cleaned_records:
                    async with get_async_session() as session:
                        await session.execute(
                            insert(model_class),
                            cleaned_records
                        )
                        await session.commit()
                        result = await run_sigma_on_batch(session, category=cat, rows=records)
                        if result["findings"]:
                            print(f"[sigma-batch] {cat}: {result['findings']} alert(s) "
                                f"from {result['batch_size']} events")
    except Exception as e:
        print(f"Failed to psuh data in db {str(e)}")
