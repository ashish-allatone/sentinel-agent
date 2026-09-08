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

    curr_services = {}
    ser_list = []
    for s in res:
        this_ser = {
            "service_name" : s.service_name,
            "username" : s.user_name,
            "password" : s.password_enc,
            "is_enable" : s.is_active
        }
        curr_services[s.agent_name] = this_ser
        ser_list.append(s.agent_name)

    engines_list = []

    for en in engines:
        this_en = AvailableEngines(engine = en )
        if en in ser_list:
            curr_ser = curr_services.get(en)
            this_en.service_name = curr_ser.get("service_name")
            this_en.username = curr_ser.get("username")
            this_en.password = curr_ser.get("password")
            this_en.is_enable = curr_ser.get("is_enable")
        
        engines_list.append(this_en)

    data_res = AvailableEnginesResponse(available_engines=engines_list)
    return standard_success_response(data = data_res , message = "Available services fetched successfully")








@agent_management_router.get("/toggle_status",  status_code=200)
async def toggle_Status(agent_name: str = Query() ,action: str = Query()):
    agent_name = agent_name.strip()
    action = action.strip()

    result = await mqtt_request(agent_name=agent_name, command = "update_status" , args = {"status" : action})
    
    if result is None:
        raise HTTPException(504, "Agent did not respond (may be offline)")
    return result 
    




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
    starting_args = {
        "engine": req.engine,
        "user_name":req.user_name,
        "password": req.password,
        "service_name": req.service_name,
        "dbname": req.dbname,
        "host": req.host,
        "port": req.port
    }
    await mqtt_request(agent_name=req.agent_name, command="stop_engine",args={"engine" : req.engine , "service_name" : req.service_name}) #, timeout=10.0)
    result = await mqtt_request(agent_name=req.agent_name, command="start_engine",args=starting_args )#, timeout=10.0)

    res_data = AddCredentialResponse(
        credential=_credential_data(credential),
        created=created)
    return standard_success_response(data=res_data,
                                     message="Credential saved successfully")


@agent_management_router.get("/get-credentials",
                             response_model=standard_success_response[GetCredentialsResponse],
                             status_code=200)
async def get_credentials(engine: Optional[str] = Query(None),
                          agent_name: Optional[str] = Query(None),
                          db: AsyncSession = Depends(get_async_db),):
                        #   user: dict = Depends(verify_token)):
    """List stored credentials. Passwords are never returned."""

    query = select(CredentialStorage)
    if engine:
        query = query.where(CredentialStorage.engine == canon_engine(engine))
    if agent_name:
        query = query.where(CredentialStorage.agent_name == agent_name)

    result = await db.execute(query.order_by(CredentialStorage.id))
    credentials = result.scalars().all()
    await mqtt_request(agent_name=agent_name, command="stop_engine",args={"engine" : engine}) # , timeout=10.0)
    res_data = GetCredentialsResponse(
        credentials=[_credential_data(c) for c in credentials])
    return standard_success_response(data=res_data,
                                     message="Credentials fetched successfully")


@agent_management_router.delete("/delete-credential", status_code=200)
async def delete_credential(credential_id: int = Query(),
                            db: AsyncSession = Depends(get_async_db),):
                            # user: dict = Depends(verify_token)):
    """Remove a stored credential."""

    credential = await db.get(CredentialStorage, credential_id)
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")
    result = await mqtt_request(agent_name=credential.agent_name, command="stop_engine",args={"engine" : credential.engine} , timeout=10.0)
    print(result)
    await db.delete(credential)
    await db.commit()

    return standard_success_response(data={"id": credential_id},
                                     message="Credential deleted successfully")
@agent_management_router.post("/stop-db", status_code=200)
async def stop_db(engine: str =Query(),agent_name: str = Query(),
                   db: AsyncSession = Depends(get_async_db)):
    """Stop Fly.io monitoring on an agent."""
   
    result = await mqtt_request(agent_name=agent_name, command="stop_engine",
                                args={"engine" : engine} ) #,timeout=10.0)
    if result is None:
        raise HTTPException(504, "Agent did not respond (may be offline)")
    return standard_success_response(data={"result": result},
                                     message="db monitoring stopped")

def _web_config_data(row) -> "WebConfigData":
    """Row -> response model. The password is never included."""
    return WebConfigData(
        id=row.id,
        agent_name=row.agent_name,
        server=row.server,
        target_name=row.target_name,
        host=row.host,
        port=row.port,
        status_url=row.status_url,
        access_log=row.access_log,
        error_log=row.error_log,
        tls_hosts=load_tls_hosts(row.tls_hosts),
        user_name=row.user_name,
        has_password=bool(row.password_enc),
        is_active=row.is_active,
    )


def require_agent_token(x_agent_token: Optional[str] = Header(default=None)):
    """Guard for the endpoint that hands back decrypted passwords."""
    expected = os.getenv("AGENT_API_TOKEN")
    if not expected:
        raise HTTPException(status_code=500,
                            detail="AGENT_API_TOKEN is not configured on the server")
    if x_agent_token != expected:
        raise HTTPException(status_code=401,
                            detail="invalid or missing X-Agent-Token")
    return True


# ── save (upsert) ───────────────────────────────────
@agent_management_router.post("/add-web-config",
                              response_model=standard_success_response[AddWebConfigResponse],
                              status_code=201)
async def add_web_config(req: AddWebConfigRequest,
                         db: AsyncSession = Depends(get_async_db)):
    """Store status_url / log paths / tls_hosts for an nginx or apache server."""

    # Without at least one of these there is nothing for the probe to inspect.
    if not (req.status_url or req.access_log or req.error_log or req.tls_hosts):
        raise HTTPException(
            status_code=422,
            detail="provide at least one of status_url, access_log, "
                   "error_log or tls_hosts")

    # Re-posting the same target updates it instead of creating a duplicate.
    result = await db.execute(
        select(WebInspectConfig).where(
            WebInspectConfig.agent_name == req.agent_name,
            WebInspectConfig.server == req.server,
            WebInspectConfig.host == req.host,
            WebInspectConfig.target_name == req.target_name))
    row = result.scalars().first()

    created = row is None
    if created:
        row = WebInspectConfig(agent_name=req.agent_name, server=req.server,
                               host=req.host, target_name=req.target_name)
        db.add(row)

    row.port = req.port
    row.status_url = req.status_url
    row.access_log = req.access_log
    row.error_log = req.error_log
    row.tls_hosts = dump_tls_hosts(req.tls_hosts)
    row.user_name = req.user_name
    row.is_active = req.is_active
    # if req.password is not None:
        # row.password_enc = hash_password(req.password)     # encrypted before storage

    await db.commit()
    await db.refresh(row)
    starting_args = {
            "port": req.port,
            "server":req.server,
            "host": req.host,
        }

    await mqtt_request(agent_name=req.agent_name, command="stop_web",args={"engine" : req.server}) # , timeout=10.0)
    result = await mqtt_request(agent_name=req.agent_name, command="start_web",args=starting_args) #, timeout=10.0) 
    res_data = AddWebConfigResponse(created=created,
                                    web_config=_web_config_data(row))
    return standard_success_response(
        data=res_data,
        message="Web config saved successfully" if not created
                else "Web config added successfully")


# ── list ────────────────────────────────────────────
@agent_management_router.get("/get-web-configs",
                             response_model=standard_success_response[GetWebConfigsResponse],
                             status_code=200)
async def get_web_configs(server: Optional[str] = Query(None),
                          agent_name: Optional[str] = Query(None),
                          db: AsyncSession = Depends(get_async_db)):
    """List stored web configs. Passwords are never returned."""
    stmt = select(WebInspectConfig)
    if server:
        stmt = stmt.where(WebInspectConfig.server == canon_server(server))
    if agent_name:
        stmt = stmt.where(WebInspectConfig.agent_name == agent_name)

    result = await db.execute(stmt.order_by(WebInspectConfig.id))
    rows = result.scalars().all()
    await mqtt_request(agent_name=agent_name, command="stop_web",args={"engine" : server}) # , timeout=10.0)

    res_data = GetWebConfigsResponse(
        total=len(rows),
        web_configs=[_web_config_data(r) for r in rows])
    return standard_success_response(data=res_data,
                                     message="Web configs fetched successfully")


# ── update ──────────────────────────────────────────
@agent_management_router.patch("/update-web-config/{config_id}",
                               response_model=standard_success_response[WebConfigData],
                               status_code=200)
async def update_web_config(config_id: int, req: UpdateWebConfigRequest,
                            db: AsyncSession = Depends(get_async_db)):
    """Update selected fields, including rotating the status_url password."""
    row = await db.get(WebInspectConfig, config_id)
    if not row:
        raise HTTPException(status_code=404, detail="Web config not found")

    data = req.model_dump(exclude_unset=True)
    # if "password" in data:
    #     row.password_enc = encrypt(data.pop("password"))
    if "tls_hosts" in data:
        row.tls_hosts = dump_tls_hosts(data.pop("tls_hosts"))
    for key, value in data.items():
        setattr(row, key, clean(value))

    await db.commit()
    await db.refresh(row)
    return standard_success_response(data=_web_config_data(row),
                                     message="Web config updated successfully")


# ── delete ──────────────────────────────────────────
@agent_management_router.delete("/delete-web-config/{config_id}", status_code=200)
async def delete_web_config(config_id: int,
                            db: AsyncSession = Depends(get_async_db)):
    row = await db.get(WebInspectConfig, config_id)
    if not row:
        raise HTTPException(status_code=404, detail="Web config not found")
    await db.delete(row)
    await db.commit()
    return standard_success_response(data={"deleted_id": config_id},
                                     message="Web config deleted successfully")


# ── agent-only: control JSON for WebDiscoveryCollector ──
@agent_management_router.get("/agents/{agent_name}/web-config/resolve",
                             status_code=200)
async def resolve_web_config(agent_name: str,
                             db: AsyncSession = Depends(get_async_db),
                             _=Depends(require_agent_token)):
    
    result = await db.execute(
        select(WebInspectConfig).where(
            WebInspectConfig.is_active.is_(True),
            (WebInspectConfig.agent_name == agent_name) |
            (WebInspectConfig.agent_name.is_(None))))
    rows = result.scalars().all()
    return build_control_json(rows)

    
@agent_management_router.post("/start-fly", status_code=200)
async def start_fly(req: FlyStartRequest,
                    db: AsyncSession = Depends(get_async_db)):
    """Start Fly.io monitoring on an agent.
 
    local: send only want_* (+ org). api: send token in the body (+ org).
    """
    backend = (req.backend or "").lower()
 
    # build the detail dict from ONLY what the user sent (drop None)
    detail = {k: v for k, v in req.model_dump(exclude_none=True).items()
              if k != "agent_name"}
    prefer=req.prefers
    # API backend must carry its token from the body — never fall back to env.
    if prefer == "api" and backend == "api" or (req.token and not backend):
        detail["backend"] = "api"
        if not req.token:
            raise HTTPException(status_code=422,
                                detail="api backend requires 'token' in the request body")
    # local / docker: leave backend as-is; the agent auto-fills fly_bin on detect.
 
    # same stop-then-start rhythm you use for engines/web
    # print(backend)
    await mqtt_request(agent_name=req.agent_name, command="stop_fly",
                       args={"engine" : backend} )#, timeout=10.0)
    # print(detail)
    result = await mqtt_request(agent_name=req.agent_name, command="start_fly",
                                args={"detail": detail}) #timeout=15.0)
    if result is None:
        raise HTTPException(504, "Agent did not respond (may be offline)")
 
    return standard_success_response(data={"detail": detail, "result": result},
                                     message="Fly monitoring started")
 
 
@agent_management_router.post("/stop-fly", status_code=200)
async def stop_fly(engine: str =Query(),agent_name: str = Query(),
                   db: AsyncSession = Depends(get_async_db)):
    """Stop Fly.io monitoring on an agent."""
   
    result = await mqtt_request(agent_name=agent_name, command="stop_fly",
                                args={"engine" : engine} ,timeout=10.0)
    if result is None:
        raise HTTPException(504, "Agent did not respond (may be offline)")
    return standard_success_response(data={"result": result},
                                     message="Fly monitoring stopped")

@agent_management_router.post("/start-appserver", status_code=200)
async def start_appserver(req: AppServerStartRequest,
                           db: AsyncSession = Depends(get_async_db)):
    """Start WildFly/JBoss monitoring on an agent.
 
    local api  : {"agent_name","backend":"api","user","password"}         (host defaults 127.0.0.1)
    remote api : {"agent_name","backend":"api","host","user","password"}
    local cli  : {"agent_name","backend":"cli"}                            (cli_path optional)
    """
    backend = (req.backend or "api").lower()
 
    detail = {k: v for k, v in req.model_dump(exclude_none=True).items()
              if k != "agent_name"}
    detail.setdefault("host", "127.0.0.1")
    detail.setdefault("port", 9990)
    detail["backend"] = backend
    # api backend to a REMOTE host needs mgmt credentials from the body
    is_remote = detail["host"] not in ("127.0.0.1", "localhost", "::1")
    if backend == "api" and is_remote and not req.user:
        raise HTTPException(status_code=422,
                            detail="remote api backend requires mgmt 'user' (and 'password') in the body")
 
    # same stop-then-start rhythm as your other collectors
    await mqtt_request(agent_name=req.agent_name, command="stop_appserver",
                       args={"source": _appserver_source_id(detail)}) #, timeout=10.0)
    result = await mqtt_request(agent_name=req.agent_name, command="start_appserver",
                                args={"detail": detail})#, timeout=15.0)
    if result is None:
        raise HTTPException(504, "Agent did not respond (may be offline)")
 
    # don't echo the password back
    safe = {k: v for k, v in detail.items() if k != "password"}
    return standard_success_response(data={"detail": safe, "result": result},
                                     message="App-server monitoring started")
 
 
@agent_management_router.post("/stop-appserver", status_code=200)
async def stop_appserver(agent_name: str = Query(),
                         source: Optional[str] = Query(default=None),
                         db: AsyncSession = Depends(get_async_db)):
    """Stop app-server monitoring. Pass the source id to stop one, or omit for all."""
    result = await mqtt_request(agent_name=agent_name, command="stop_appserver",
                                args={"source":_appserver_source_id({"backend":source})} if source else {}, timeout=10.0)
    if result is None:
        raise HTTPException(504, "Agent did not respond (may be offline)")
    return standard_success_response(data={"result": result},
                                     message="App-server monitoring stopped")
 
 
def _appserver_source_id(detail: dict) -> str:
    """Mirror AppServerInspector._source_id so stop targets the right thread."""
    server = (detail.get("server") or "wildfly").lower()
    host = detail.get("host") or "127.0.0.1"
    port = int(detail.get("port") or 9990)
    backend = (detail.get("backend") or "api").lower()
    return f"{server}:{backend}:{host}:{port}"