import json
from confluent_kafka import Producer
from datetime import datetime


class KafkaWriter:
    def __init__(self, brokers: str, topic: str):
        self.topic = topic

        self.producer = Producer({
            "bootstrap.servers": brokers,
            "acks": "all",
            "compression.type": "snappy",
            "queue.buffering.max.messages": 100000,
            "linger.ms": 50,
            "batch.num.messages": 1000,
        })
        
    def delivery_report(self, err, msg):
        if err is not None:
            print(f"Kafka delivery failed: {err}")

    def write(self, event: dict):
        def default_serializer(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Type not serializable: {type(obj)}")

        payload = json.dumps(event, default=default_serializer).encode("utf-8")
        try:
            self.producer.produce(
                topic=self.topic,
                value=payload,
                callback=self.delivery_report
            )

            # Trigger delivery callbacks
            self.producer.poll(0)

        except BufferError:
            print("Kafka producer queue full")

        except Exception as e:
            print(f"Kafka write error: {e}")

    def flush(self):
        self.producer.flush()