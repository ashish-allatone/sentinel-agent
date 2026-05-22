from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.v1 import v1_api_router
from db.db import create_db_and_tables

from bots.kafka_consumer import kafka_consumer_service


consumer_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):

    global consumer_task

    # # DATABASE STARTUP
    await create_db_and_tables()

    # START KAFKA CONSUMER
    kafka_consumer_service.start()

    consumer_task = asyncio.create_task(
        kafka_consumer_service.consume_loop()
    )

    print("Application Started")

    yield

    # SHUTDOWN LOGIC
    print("Application Shutting Down...")

    await kafka_consumer_service.shutdown()

    if consumer_task:
        consumer_task.cancel()


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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False
    )
