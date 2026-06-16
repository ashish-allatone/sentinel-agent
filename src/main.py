from contextlib import asynccontextmanager
import asyncio
from platform import architecture

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.v1 import v1_api_router
from db.db import create_db_and_tables

from bots.mqtt_consumer import mqtt_background_consumer


worker_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):

    global worker_task

    # # DATABASE STARTUP
    await create_db_and_tables()


    worker_task = asyncio.create_task(mqtt_background_consumer())

    print("Application Started")

    yield

    # SHUTDOWN LOGIC
    print("Application Shutting Down...")
    worker_task.cancel()


app = FastAPI(
    lifespan=lifespan
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(v1_api_router , prefix="/v1")


@app.get("/")
def root():
    return {
        "APPLICATION": "RUNNING"
    }


@app.get("/healthCheck")
def health_check():
    return {
        "status": "Success"
    }
    
    
    
##########################################    
        #Sender api 
##########################################    

from fastapi import FastAPI, Query
import json
import paho.mqtt.publish as publish

app = FastAPI()


@app.get("/run-script/")
def run_script(
    script_type: str = Query(..., description="ps or bash"),
    command: str = Query(..., description="command to run")
):
    payload = json.dumps({
        "type": script_type,
        "command": command
    })

    publish.single(
        topic="agent/execute",
        payload=payload,
        hostname="localhost"
    )

    return {"status": "script sent"}



SCRIPTS = {
    ("windows", "x64"): {
        "type": "ps",
        "command": "Get-Process"
    },
    ("windows", "x86"): {
        "type": "ps",
        "command": "Get-Service"
    },
    ("linux", "x64"): {
        "type": "bash",
        "command": "ls -l"
    },
    ("linux", "arm"): {
        "type": "bash",
        "command": "uname -a"
    }
}

###############################################
       #get script based on OS and architecture
###############################################

from fastapi import FastAPI, Query
import json
import paho.mqtt.publish as publish

app = FastAPI()

MQTT_BROKER = "127.0.0.1"
MQTT_TOPIC = "agent/execute"


@app.get("/run")
def run_script(os: str, arch: str):
    # get script based on OS and architecture
    key = (os.lower(), arch.lower())

    script = SCRIPTS.get(key)

    if not script:
        return {"error": "Unsupported OS or architecture"}

    publish.single(
        topic=MQTT_TOPIC,
        payload=json.dumps(script),
        hostname=MQTT_BROKER,
        port=1883
    )

    return {
        "status": "sent",
        "os": os,
        "arch": arch,
        "script": script
    }



