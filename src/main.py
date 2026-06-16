from contextlib import asynccontextmanager
import asyncio
from platform import architecture

from fastapi import FastAPI , Request
from fastapi.responses import PlainTextResponse , FileResponse
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

import os
app.include_router(v1_api_router , prefix="/v1")
HERE = os.path.dirname(os.path.abspath(__file__))


@app.get("/")
def root():
    return {
        "APPLICATION": "RUNNING"
    }


@app.get("/install_service.ps1", response_class=PlainTextResponse)
def install_ps1(request : Request):
    text = open(os.path.join(HERE, "a.ps1"), encoding="utf-8").read()
    base = str(request.base_url).rstrip("/")          # e.g. https://agents.example.com
    return text.replace("https://YOUR_HOST", base)

@app.get("/binaries/{name}", response_class=FileResponse)
def install_ps1(name: str):
    safe = os.path.basename(name)                  # strips ../ to block path traversal
    path = os.path.join(HERE, safe)
    print(path)
    if not os.path.isfile(path):
        print("Not FOund")
        raise 
    return FileResponse(path, media_type="application/octet-stream", filename=safe)



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



