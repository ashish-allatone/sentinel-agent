import asyncio
import orjson
import os

from sqlalchemy import insert
from models.data_log_model import MachineLogs
from db.db import AsyncSessionLocal

from confluent_kafka import Consumer, KafkaException
from dotenv import load_dotenv

load_dotenv()

KAFKA_CONFIG = {
    "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP_SERVER"),
    "group.id": "sentinel-consumer-group",

    "enable.auto.commit": False,
    "auto.offset.reset": "latest",

    # batching optimization
    "fetch.min.bytes": 1024,
    "fetch.wait.max.ms": 500,
}


TOPIC = os.environ.get("KAFKA_TOPIC")


BATCH_SIZE = 1000
FLUSH_INTERVAL = 5

import json
from datetime import datetime

def parse_dt(v):
    if isinstance(v, str):
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v

def parse_raw_log(l):
    if isinstance(l , dict):
        return json.dumps(l)
    
    return l


class KafkaConsumerService:

    def __init__(self):

        self.running = True
        self.consumer = None

        self.message_buffer = []

    def start(self):

        self.consumer = Consumer(KAFKA_CONFIG)

        self.consumer.subscribe([TOPIC])

        print("Kafka Consumer Started")


    async def insert_events_bulk(self , events: list[dict]):
        async with AsyncSessionLocal() as session:
            try:
                await session.execute(
                    insert(MachineLogs),
                    events
                )
                await session.commit()

            except Exception as e:
                await session.rollback()
                print("DB Insert Error:", e)
                raise

    async def process_batch(self, messages):

        parsed_messages = []

        for msg in messages:
            try:
                raw = msg.value()
                if not raw:
                    continue
                data = orjson.loads(raw)
            except Exception as parse_err:
                print(f"Skipping bad message: {parse_err} | raw: {msg.value()}")
                continue
            # data = orjson.loads(msg.value())
            # transform into DB format
            parsed_messages.append({
                "machine_id" : data.get("machine_id"),
                "event_id": data.get("event_id"),
                "timestamp": parse_dt(data.get("timestamp")),
                "ingested_at": parse_dt(data.get("ingested_at")),

                "category": data.get("category"),
                "action": data.get("action"),
                "outcome": data.get("outcome"),
                "severity": data.get("severity"),

                "collector": data.get("collector"),
                "host": str(data.get("host")),
                "raw_log": parse_raw_log(data.get("raw_log")),

                "file_path": data.get("file", {}).get("path"),
                "file_sha256": data.get("file", {}).get("sha256"),

                "process_name": data.get("process", {}).get("name"),
                "process_pid": data.get("process", {}).get("pid"),

                "net_src_ip": data.get("network", {}).get("src_ip"),
                "net_dst_ip": data.get("network", {}).get("dst_ip"),
                "net_dst_port": data.get("network", {}).get("dst_port"),

                "risk_score": data.get("risk_score"),
                "anomaly": data.get("anomaly"),
                "ioc_match": data.get("ioc_match"),
                "mitre_tactic": data.get("mitre_tactic"),
                "mitre_technique": data.get("mitre_technique"),
                "notes": data.get("notes"),
            })
        if not parsed_messages:
            return
        print("inserting")

        await self.insert_events_bulk(parsed_messages)
        print("inserted")


    async def flush(self):

        if not self.message_buffer:
            return

        batch = self.message_buffer

        self.message_buffer = []

        try:

            # PROCESS WHOLE BATCH
            await self.process_batch(batch)

            # COMMIT ONLY AFTER SUCCESS
            self.consumer.commit(
                asynchronous=False
            )

            print(f"Committed {len(batch)} messages")

        except Exception as e:

            print("Batch Processing Error:", e)

            # restore messages if failed
            self.message_buffer.extend(batch)

    async def consume_loop(self):

        while self.running:

            try:

                messages = self.consumer.consume(
                    num_messages=BATCH_SIZE,
                    timeout=1.0
                )

                if not messages:
                    await asyncio.sleep(0.1)
                    continue

                valid_messages = []

                for msg in messages:

                    if msg.error():
                        print("Kafka Message Error:", msg.error())
                        continue

                    valid_messages.append(msg)

                if not valid_messages:
                    continue

                await self.process_batch(valid_messages)

                self.consumer.commit(
                    asynchronous=False
                )

                print(f"Committed {len(valid_messages)} messages")

            except Exception as e:

                print("Kafka Consumer Error:", e)

                await asyncio.sleep(2)

    async def shutdown(self):

        print("Stopping Kafka Consumer...")

        self.running = False

        # flush remaining messages
        await self.flush()

        if self.consumer:
            self.consumer.close()


kafka_consumer_service = KafkaConsumerService()