from sqlalchemy.future import select
from fastapi import APIRouter , Depends , HTTPException , status , Query
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
import base64
from sqlalchemy import desc ,select, func

###############################################
#                                             #
#              LOCAL MODULES IMPORT           #
#                                             #
###############################################
from db.db import  get_async_db
from schemas.v1.standard_schema import standard_success_response
from schemas.v1.agent_management_schema import (
    GetAgentsResponse , AgentData,
    GetAgentDataResponse
)

from models.master_model import User , AgentDBData
from models.data_log_model import MachineLogs

from auth.jwt_auth import verify_token




agent_management_router = APIRouter()

@agent_management_router.get("/get-agents" , response_model = standard_success_response[GetAgentsResponse] , status_code = 200)
async def getAgents():
    # user:dict = Depends(verify_token)
    
    agents_list = []
    async with get_async_db("master_database") as db:
        result = await db.execute(select(AgentDBData))
        existing_agents = result.scalars().all()

        for agent in existing_agents:
            curr_agent = AgentData(
                id = agent.id,
                agent_name = agent.agent_name,
                mac_address = agent.mac_address, 
                host_name = agent.host_name,
                main_ip = agent.main_ip,
                all_ips = agent.all_ips,
                system = agent.system,
                release = agent.release,
                version = agent.version,
                machine_architecture = agent.machine_architecture,
                is_active = agent.is_active

            )
            agents_list.append(curr_agent)
    res_data = GetAgentsResponse(agents = agents_list)
    
    return standard_success_response(data = res_data , message = "Agents Data Fetched successfully")








# @agent_management_router.get("/get-agent-data" , response_model = standard_success_response[GetAgentDataResponse] , status_code = 200)
# async def getAgents(agent_name:str ):
#     #  user:dict = Depends(verify_token)
#     agent_data = []
#     agent_name = agent_name.strip()
#     async with get_async_db(agent_name) as db:
#         print("sdfghjk")
#         result = await db.execute(select(MachineLogs).order_by(desc(MachineLogs.id)))
#         db_logs = result.mappings().all()
#         print("sdfghjk")

#         agent_data = [dict(row)["MachineLogs"].__dict__ for row in db_logs]
#         print("sdfghjk")
        
#         # Clean up internal SQLAlchemy state tracking keys if necessary
#         for d in agent_data:
#             d.pop('_sa_instance_state', None)
#         print("sdfghjk")

#     res_data = GetAgentDataResponse(agent_data = agent_data)
    
#     return standard_success_response(data = res_data , message = f"Agent {agent_name} Data Fetched successfully")



@agent_management_router.get("/get-agent-data", response_model=standard_success_response[GetAgentDataResponse], status_code=200)
async def getAgents(
    agent_name: str,
    page: int = Query(default=1, ge=1, description="Page number, starting from 1"),
):
    agent_name = agent_name.strip()
    limit = 100
    offset = (page - 1) * limit

    async with get_async_db(agent_name) as db:
        # count_query = select(func.count()).select_from(MachineLogs)
        # total_records = (await db.execute(count_query)).scalar()

        # 2. Fetch paginated logs using limit and offset
        query = (
            select(MachineLogs)
            .order_by(desc(MachineLogs.id))
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(query)
        db_logs = result.mappings().all()

        # 3. Transform data efficiently
        agent_data = []
        for row in db_logs:
            log_obj = row["MachineLogs"]
            # Convert to dict and safely remove SQLAlchemy internal state
            log_dict = {k: v for k, v in log_obj.__dict__.items() if k != '_sa_instance_state'}
            agent_data.append(log_dict)

    # 4. Construct response
    res_data = GetAgentDataResponse(agent_data=agent_data)
    
    return standard_success_response(
        data=res_data, 
        message=f"Agent {agent_name} Data Fetched successfully (Page {page})"
    )