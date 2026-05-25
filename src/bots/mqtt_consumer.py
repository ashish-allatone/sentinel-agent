import asyncio
import json
import aiomqtt
from db.db import push_data_to_db_by_category, check_db_exists, create_agent_db_and_tables, register_new_agent
 
SERVER_IP  = "80.225.239.163"
MQTT_USER  = "my_mqtt_user"
MQTT_PASS  = "mqttpassword"
TOPIC      = "agent/events"
BATCH_SIZE = 100
 
# Category constants — must match EventCategory enum in agent
CATEGORY_FILE    = "file"
CATEGORY_NETWORK = "network"
CATEGORY_PROCESS = "process"
CATEGORY_AUTH    = "authentication"
CATEGORY_SYSTEM  = "system"
 
ALL_CATEGORIES = [
    CATEGORY_FILE,
    CATEGORY_NETWORK,
    CATEGORY_PROCESS,
    CATEGORY_AUTH,
    CATEGORY_SYSTEM,
]
 
 
def _empty_batch():
    """Returns a fresh dict with one list per category."""
    return {cat: [] for cat in ALL_CATEGORIES}
 
 
async def mqtt_background_consumer():
    # master_dict[agent_name] = { "meta_data": {}, "batches": { "file": [], "network": [], ... } }
    master_dict = {} 
    while True:
        try:
            async with aiomqtt.Client(
                hostname=SERVER_IP,
                port=1883,
                username=MQTT_USER,
                password=MQTT_PASS,
            ) as client:
                await client.subscribe(TOPIC)
                print(f" Consumer connected to and Listening to: {TOPIC}")
 
                async for message in client.messages:
                    # ── 1. Parse incoming message ──────────────────
                    try:
                        data_dict   = json.loads(message.payload.decode("utf-8"))
                    except Exception as e:
                        print(f"Bad message skipped: {e}")
                        continue
 
                    machine_info = data_dict.get("machine_info", {})
                    event_data   = data_dict.get("event", {})
                    agent_name   = machine_info.get("agent_name")
 
                    if not agent_name:
                        continue
 
                    # ── 2. Init agent slot on first seen ───────────
                    if agent_name not in master_dict:
                        master_dict[agent_name] = {
                            "meta_data": machine_info,
                            "batches":   _empty_batch(),
                        }
 
                    # ── 3. Route event to correct category bucket ──
                    category = event_data.get("category", CATEGORY_SYSTEM)
                    if category not in ALL_CATEGORIES:
                        category = CATEGORY_SYSTEM   # fallback
 
                    master_dict[agent_name]["batches"][category].append(event_data)
 
                    # ── 4. Flush any category that hit BATCH_SIZE ──
                    for cat in ALL_CATEGORIES:
                        bucket = master_dict[agent_name]["batches"][cat]
                        if len(bucket) >= BATCH_SIZE:
                            await push_data_to_db_by_category(
                                agent_name   = agent_name,
                                meta_data    = master_dict[agent_name]["meta_data"],
                                category     = cat,
                                log_data     = bucket,
                            )
                            master_dict[agent_name]["batches"][cat] = []
        except aiomqtt.MqttError as error:
            print(f"\n Network error: {error}. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)