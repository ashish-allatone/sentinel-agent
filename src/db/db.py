from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from db.base import Base
from models.user_model import Users
from models.machines_model import Machines
from models.data_log_model import MachineLogs
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv
import os

load_dotenv()




"""
Database Configuration and Initialization Module.

This module sets up both synchronous and asynchronous database engines using SQLAlchemy.
It includes:
- Connection strings for PostgreSQL
- Session factories for both sync and async usage
- Utility functions to get database sessions
- A function to create tables and insert static reference data if not already present

Environment variables and AWS parameter store secrets are used for securely injecting credentials.

Functions:
-----------
- get_db():
    Provides a synchronous session scope for use with traditional SQLAlchemy operations.

- get_async_db():
    Async generator function that yields an `AsyncSession` for database operations in an async context.

- create_db_and_tables():
    Initializes all database tables from SQLAlchemy models and inserts static reference data 
    such as statuses, roles, fuel types, model types, and others required for the system.

Raises:
-------
- DatabaseError:
    Raised if any exception occurs during the creation of tables or insertion of data.

Examples:
---------
>>> async with get_async_db() as session:
>>>     result = await session.execute(select(SomeModel))
>>>     data = result.scalars().all()

>>> await create_db_and_tables()
"""
dbuser = os.environ.get("DB_USER")
dbpassword = os.environ.get("DB_PASSWORD")
dbendpoint = os.environ.get("DB_ENDPOINT")
dbname = os.environ.get("DB_NAME")



DATABASE_URL_ASYNC=f"postgresql+asyncpg://{dbuser}:{dbpassword}@{dbendpoint}:5432/{dbname}"


async_engine: AsyncEngine = create_async_engine(DATABASE_URL_ASYNC)

AsyncSessionLocal = sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False
)



async def get_async_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
            

            
            
            
async def create_db_and_tables():
    """
    Initializes the database and creates the necessary tables, inserting predefined data into various tables 
    if no records already exist for the specified values.

    This function will create the database tables defined in the `Base.metadata`, then proceed to insert default values 
    into predefined tables like `VehicleStatus`, `TaskStatus`, `DriverRoles`, `FuelTypes`, `AttendanceStates`, `UnlockStates`, 
    `DriverVehicleConnectedTimeStates`, `ModelTypes`, `FuelBaseCosting`, `RequestStatus`, `LeaveTypes`, and `ClientMain` 
    based on predefined lists and dictionaries. If any of these records already exist, they are not added again.

    **Procedure:**
    1. Create tables defined in the metadata.
    2. Check if predefined data exists in the respective tables.
    3. Insert predefined data where no records exist.

    :raises DatabaseError: If an error occurs while creating tables or inserting data.
    """
    
    
    
    print(f"Running with Database ..")
    async with async_engine.begin() as conn:
        try:        
            await conn.run_sync(Base.metadata.create_all)
        except SQLAlchemyError as e:
            raise RuntimeError(message= f"Failed to initialize tables with data: {str(e)}")      
              