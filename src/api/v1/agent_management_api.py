from sqlalchemy.future import select
from fastapi import APIRouter , Depends , HTTPException , status , Query, Header

from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
import base64
import os
from typing import Optional , List
from sqlalchemy import desc ,select, func

###############################################
#                                             #
#              LOCAL MODULES IMPORT           #
#                                             #
###############################################
from db.db import  get_async_db
from schemas.v1.standard_schema import standard_success_response
from schemas.v1.agent_management_schema import (
    GetAgentsResponse , AgentData, AgentStatusCount,
    AgentGroupCount , AgentOSCount, IsValidAgentNameResponse,
    ExistingGroup , ExistingGroupsResponse , AgentInstallationCommandResponse,
    AvailableEnginesResponse , AvailableEngines
)

from models.agent_model import Agents , AgentGroups , ServicesCredentials

from auth.jwt_auth import verify_token , verify_admin_token
from utils.mqtt_utils import mqtt_request
from models.credential_model import CredentialStorage,WebInspectConfig
from schemas.v1.agent_management_schema import (
        AddCredentialRequest, AddCredentialResponse,
        GetCredentialsResponse, CredentialData,AddWebConfigRequest,
        UpdateWebConfigRequest,WebConfigData,AddWebConfigResponse,
        GetWebConfigsResponse,FlyStartRequest,AppServerStartRequest)
from utils.web_config import (canon_server, clean, load_tls_hosts,
                                  dump_tls_hosts, build_control_json)
from auth.crypto import hash_password
from utils.crypto import canon_engine


agent_management_router = APIRouter()






@agent_management_router.get("/get-agents" , response_model = standard_success_response[GetAgentsResponse] , status_code = 200)
async def getAgents(db: AsyncSession = Depends(get_async_db) , user:dict = Depends(verify_token)):
    
    
    agents_list = []
    group_dict = {}
    agent_group_count = {}
    agent_status_count = {}
    agent_os_count = {}

    agent_status_count["total"] = 0


    group_result = await db.execute(select(AgentGroups))
    existing_groups = group_result.scalars().all()
    group_dict = {g.id : g.group_name for g in existing_groups}

    result = await db.execute(select(Agents))
    existing_agents = result.scalars().all()


    for agent in existing_agents:
        g_name = group_dict.get(agent.group_id , None)
        if g_name:
            if agent_group_count.get(g_name):
                agent_group_count[g_name] +=1
            else:
                agent_group_count[g_name] = 1

        agent_os = agent.os
        agent_os = agent_os.lower() if agent_os else None
        if agent_os and agent_os_count.get(agent_os):
            agent_os_count[agent_os] +=1
        elif agent_os:
            agent_os_count[agent_os] = 1

        status = agent.status
        if status:
            if agent_status_count.get(status):
                agent_status_count[status] +=1
            else:
                agent_status_count[status] = 1
            
            agent_status_count["total"] +=1

        curr_agent = AgentData(
            id = agent.id,
            agent_name = agent.agent_name,
            mac_address = agent.mac_address, 
            host_name = agent.host_name,
            main_ip = agent.main_ip,
            all_ips = agent.all_ips,
            os = agent.os,
            release = agent.release,
            version = agent.version,
            machine_architecture = agent.machine_architecture,
            is_active = agent.is_active,
            status = agent.status,
            group_name = g_name

        ) 
        agents_list.append(curr_agent)
    agent_os_count = [AgentOSCount(os_name = k , os_count = v) for k , v in agent_os_count.items()]
    agent_group_count = [AgentGroupCount(group_name = k , group_count = v) for k , v in agent_group_count.items()]
    agent_status_count = AgentStatusCount(
        total = agent_status_count.get("total"),
        active = agent_status_count.get("active" , 0),
        disconnected = agent_status_count.get("disconnected" , 0),
        pending = agent_status_count.get("pending" , 0),
        never_connected = agent_status_count.get("never_connected" , 0),
    )
    res_data = GetAgentsResponse(
        agent_status_count = agent_status_count,
        agent_os_count = agent_os_count,
        agent_group_count = agent_group_count,
        agents = agents_list
    )
    
    return standard_success_response(data = res_data , message = "Agents Data Fetched successfully")








@agent_management_router.get("/available-services", response_model = standard_success_response[AvailableEnginesResponse] , status_code=200)
async def get_available_services(agent_name: str = Query() ,  db: AsyncSession = Depends(get_async_db)):
    agent_name = agent_name.strip()

    result = await mqtt_request(agent_name=agent_name, command =  "list_services") #,timeout=20.0)
    
    if result is None:
        raise HTTPException(504, "Agent did not respond (may be offline)")
    
    result = result.get("result")
    engines = [r.get("engine") for r in result]
    res = await db.execute(select(CredentialStorage).where(CredentialStorage.agent_name == agent_name))
    res = res.scalars().all()

    curr_services = {e : [] for e in engines}
    for s in res:
        print(s.service_name)
        engine = s.engine
        this_service = {
            "service_name" : s.service_name,
            "is_enable" : s.is_active
        }
        curr_services[engine].append(this_service)

    data_res = AvailableEnginesResponse(available_engines=curr_services)
    return standard_success_response(data = data_res , message = "Available services fetched successfully")








@agent_management_router.get("/toggle_status", response_model = standard_success_response , status_code=200)
async def toggle_Status(agent_name: str = Query() ,action: str = Query()):
    agent_name = agent_name.strip()
    action = action.strip()
    valid_actions = ["pause" , "active"]
    if action in valid_actions:

        result = await mqtt_request(agent_name=agent_name, command = "update_status" , args = {"status" : action})
        
        if result is None:
            raise HTTPException(504, "Agent did not respond (may be offline)")
        return standard_success_response(data = result , message = "Status changed successfully")

    else:
        raise  HTTPException(400, "Invalid command , accepts: [pause , active]")
    


@agent_management_router.get("/running_threads", response_model = standard_success_response , status_code=200)
async def getRunningThreads(agent_name: str = Query()):
    agent_name = agent_name.strip()

    result = await mqtt_request(agent_name=agent_name, command = "list_threads")
    
    if result is None:
        raise HTTPException(504, "Agent did not respond (may be offline)")
    return standard_success_response(data = result , message = "got running threads successfully")

   




@agent_management_router.get("/is-valid-agent-name" ,  response_model = standard_success_response[IsValidAgentNameResponse] , status_code = 200)
async def is_valid_agent_name(agent_name:str = Query() , db: AsyncSession = Depends(get_async_db)):

    agent_name = agent_name.strip()
    result = await db.execute(select(Agents).where(Agents.agent_name == agent_name))
    existing_user = result.scalars().first()

    if existing_user:
        raise HTTPException(status_code=401, detail="Agent already exists with this name")
    
    res_data = IsValidAgentNameResponse(valid = True)

    return standard_success_response(data = res_data , message = "Agents name is Valid")




@agent_management_router.get("/existing-groups" ,  response_model = standard_success_response[ExistingGroupsResponse] , status_code = 200)
async def existing_group(db: AsyncSession = Depends(get_async_db)):

    group_result = await db.execute(select(AgentGroups))
    existing_groups = group_result.scalars().all()

    group_list = [ExistingGroup(group_id = g.id , group_name= g.group_name) for g in existing_groups]

    res_data = ExistingGroupsResponse(groups = group_list)

    return standard_success_response(data = res_data , message = "Existing groups get Successfully")



@agent_management_router.get("/agent-installation-command" ,  response_model = standard_success_response[AgentInstallationCommandResponse] , status_code = 200)
async def agent_installation_command(os : str, 
                                     agent_name: str,
                                     server_ip : str,
                                     group_name : Optional[str] = "None",
                                     db: AsyncSession = Depends(get_async_db),
                                     user:dict = Depends(verify_admin_token)):

    linux_command = f"curl -fsSL {server_ip}:8000/api/v1/scripts/setup.sh | sudo bash -s -- --server-ip {server_ip} --agent-name {agent_name} --group-name {group_name}"
    win_command = f"$env:SERVER_IP='{server_ip}'; $env:AGENT_NAME='{agent_name}'; $env:GROUP_NAME='{group_name}'; irm {server_ip}:8000/api/v1/scripts/windows_install.ps1 | iex"
    res_date = None
    if os == "windows":
        res_data = AgentInstallationCommandResponse(installation_command = win_command)
    else:
        res_data = AgentInstallationCommandResponse(installation_command = linux_command)
    
       

    return standard_success_response(data = res_data , message = "Instalation command get Successfully")

def _credential_data(row) -> "CredentialData":
    """Row -> response model. The password is never included."""
    return CredentialData(
        id=row.id,
        agent_name=row.agent_name,
        engine=row.engine,
        host=row.host,
        port=row.port,
        user_name=row.user_name,
        service_name=row.service_name,
        dbname=row.dbname,
        is_active=row.is_active,
        has_password=bool(row.password_enc),
    )


CATEGORIES = {"databases" : ["postgresql", "mysql", "mariadb", "oracle", "redis", "mongodb"] ,
            "web_servers" : ["nginx" , "apache" , "httpd" , "nginx.exe"] , "app_servers" : ["jboss" , "wildfly"]}
@agent_management_router.post("/add-credential",
                              response_model=standard_success_response[AddCredentialResponse],
                              status_code=201)
async def add_credential(req: AddCredentialRequest,
                         db: AsyncSession = Depends(get_async_db),):
                        #  user: dict = Depends(verify_token)):
    """Store user_name / password / service_name / dbname for an engine."""

    # Oracle needs one of these to build a connect string.
    if req.engine == "oracle" and not (req.service_name or req.dbname):
        raise HTTPException(status_code=422,
                            detail="service_name is required for oracle")

    # Re-posting the same target updates it instead of creating a duplicate.
    result = await db.execute(
        select(CredentialStorage).where(
            CredentialStorage.agent_name == req.agent_name,
            CredentialStorage.engine == req.engine,
            CredentialStorage.host == req.host,
            CredentialStorage.service_name == req.service_name))
    credential = result.scalars().first()

    created = credential is None
    if created:
        credential = CredentialStorage(
            agent_name=req.agent_name,
            engine=req.engine,
            host=req.host,
            service_name=req.service_name)
        db.add(credential)

    credential.user_name = req.user_name
    credential.dbname = req.dbname
    credential.port = req.port
    credential.is_active = True
    if req.password is not None:
        credential.password_enc = hash_password(req.password)     # encrypted here

    await db.commit()
    await db.refresh(credential)
    category = "databases" if req.engine in CATEGORIES["databases"] else None
    category = "web_servers" if req.engine in CATEGORIES["web_servers"] else category
    category = "app_servers" if req.engine in CATEGORIES["app_servers"] else category
    starting_args = {
        "category" : category,
        "engine": req.engine,
        "user_name":req.user_name,
        "password": req.password,
        "service_name": req.service_name,
        "dbname": req.dbname,
        "host": req.host,
        "port": req.port
    }
    await mqtt_request(agent_name=req.agent_name, command="stop",args={"engine" : req.engine , "service_name" : req.service_name}) #, timeout=10.0)
    result = await mqtt_request(agent_name=req.agent_name, command="start",args=starting_args )#, timeout=10.0)

    res_data = AddCredentialResponse(
        credential=_credential_data(credential),
        created=created)
    return standard_success_response(data=res_data,
                                     message="Credential saved successfully")


# @agent_management_router.get("/get-credentials",
#                              response_model=standard_success_response[GetCredentialsResponse],
#                              status_code=200)
# async def get_credentials(engine:str = Query(),
#                           service_name : str = Query(),
#                           agent_name:str = Query(),
#                           db: AsyncSession = Depends(get_async_db),):
#                         #   user: dict = Depends(verify_token)):
#     """List stored credentials. Passwords are never returned."""

#     query = select(CredentialStorage)
#     if engine:
#         query = query.where(CredentialStorage.engine == canon_engine(engine))
#     if agent_name:
#         query = query.where(CredentialStorage.agent_name == agent_name)

#     result = await db.execute(query.order_by(CredentialStorage.id))
#     credentials = result.scalars().all()
#     await mqtt_request(agent_name=agent_name, command="stop_engine",args={"engine" : engine}) # , timeout=10.0)
#     res_data = GetCredentialsResponse(
#         credentials=[_credential_data(c) for c in credentials])
#     return standard_success_response(data=res_data,
#                                      message="Credentials fetched successfully")


# @agent_management_router.delete("/delete-credential", status_code=200)
# async def delete_credential(service_name: str = Query(),
#                             db: AsyncSession = Depends(get_async_db),):
#                             # user: dict = Depends(verify_token)):
#     """Remove a stored credential."""

#     credential = await db.get(CredentialStorage)
#     if not credential:
#         raise HTTPException(status_code=404, detail="Credential not found")
#     category = "databases" if req.engine in CATEGORIES["databases"] else None
#     category = "web_servers" if req.engine in CATEGORIES["web_servers"] else category
#     category = "app_servers" if req.engine in CATEGORIES["app_servers"] else category
#     result = await mqtt_request(agent_name=credential.agent_name, command="stop",args={"engine" : credential.engine ,  "service_name" : credential.service_name} , timeout=10.0)
#     print(result)
#     await db.delete(credential)
#     await db.commit()

#     return standard_success_response(data={"id": credential_id},
#                                      message="Credential deleted successfully")
