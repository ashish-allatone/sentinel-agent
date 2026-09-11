# from pydantic import BaseModel
from typing import Optional ,List
from pydantic import BaseModel, Field, field_validator
 
from utils.crypto import VALID_ENGINES, canon_engine
from utils.web_config import VALID_SERVERS, canon_server, clean

class AddAgentRequest(BaseModel):
    agent_name : str
    group_id : Optional[int] = None 


class AddAgentResponse(BaseModel):
    id : int
    agent_name : str
    group_id: Optional[int] = None





class AgentData(BaseModel):
    id : int
    agent_name : str
    mac_address : str|None
    host_name : str|None
    main_ip : str|None

    all_ips : list|None 
    os : str|None
    release : str|None
    version : str|None
    machine_architecture : str|None
    is_active : bool
    status : Optional[str] = None
    group_name: Optional[str] = None


class AgentStatusCount(BaseModel):
    total : Optional[int] = 0
    active : Optional[int] = 0
    disconnected : Optional[int] = 0
    pending : Optional[int] = 0
    never_connected : Optional[int] = 0


class AgentOSCount(BaseModel):
    os_name:str
    os_count:int


class AgentGroupCount(BaseModel):
    group_name: str
    group_count: int


class GetAgentsResponse(BaseModel):
    agent_status_count : AgentStatusCount
    agent_os_count : list[AgentOSCount]
    agent_group_count : list[AgentGroupCount]
    agents : list[AgentData]




class AvailableEngines(BaseModel):
    engine: str
    service_name : Optional[str] = None
    username:  Optional[str] = None
    password: Optional[str] = None
    is_enable : Optional[bool] = False

class AvailableEnginesResponse(BaseModel):
    available_engines : list[AvailableEngines]

class IsValidAgentNameResponse(BaseModel):
    valid : bool



class ExistingGroup(BaseModel):
    group_id:int
    group_name : str


class ExistingGroupsResponse(BaseModel):
    groups : list[ExistingGroup]


class AgentInstallationCommandResponse(BaseModel):
    installation_command : str

class AddCredentialRequest(BaseModel):
    engine: str = Field(..., description="mysql | mariadb | postgresql | oracle | redis | mongodb")
    user_name: Optional[str] = Field(None, max_length=255)
    password: Optional[str] = Field(None, description="stored encrypted, never returned")
    service_name: str = Field(None, description="Oracle service name / SID")
    dbname: Optional[str] = Field(None, description="mysql / postgres / mongo database")
    host: str = "127.0.0.1"
    port: Optional[int] = None
    agent_name: str = None
 
    @field_validator("engine")
    @classmethod
    def _check_engine(cls, v):
        e = canon_engine(v)
        if e not in VALID_ENGINES:
            raise ValueError(f"unsupported engine '{v}'; expected one of {sorted(VALID_ENGINES)}")
        return e
 
    @field_validator("port")
    @classmethod
    def _check_port(cls, v):
        if v is not None and not (1 <= v <= 65535):
            raise ValueError("port must be between 1 and 65535")
        return v
 
 
class CredentialData(BaseModel):
    id: int
    agent_name: Optional[str] = None
    engine: str
    host: str
    port: Optional[int] = None
    user_name: Optional[str] = None
    service_name: Optional[str] = None
    dbname: Optional[str] = None
    is_active: bool
    has_password: bool
 
 
class AddCredentialResponse(BaseModel):
    credential: CredentialData
    created: bool
 
 
class GetCredentialsResponse(BaseModel):
    credentials: list[CredentialData]


class AddWebConfigRequest(BaseModel):
    server: str = Field(..., description="nginx | apache")
    agent_name: Optional[str] = Field(None, description="which agent this applies to")
    target_name: Optional[str] = Field(
        None, description="name a remote target, e.g. 'edge-nginx'. "
                          "Leave empty for the agent's local server.")
    host: str = "127.0.0.1"
    port: Optional[int] = None
 
    status_url: Optional[str] = Field(
        None, description="e.g. http://127.0.0.1/nginx_status "
                          "or http://127.0.0.1/server-status?auto")
    access_log: Optional[str] = None
    error_log: Optional[str] = None
    tls_hosts: Optional[List[str]] = Field(
        None, description='certificate checks, e.g. ["example.com:443"]')
 
    user_name: Optional[str] = Field(None, description="basic-auth user for status_url")
    password: Optional[str] = Field(None, description="stored encrypted, never returned")
    is_active: bool = True
 
    @field_validator("server")
    @classmethod
    def _check_server(cls, v):
        s = canon_server(v)
        if s not in VALID_SERVERS:
            raise ValueError(f"unsupported server '{v}'; "
                             f"expected one of {sorted(VALID_SERVERS)}")
        return s
 
    @field_validator("port")
    @classmethod
    def _check_port(cls, v):
        if v is not None and not (1 <= v <= 65535):
            raise ValueError("port must be between 1 and 65535")
        return v
 
    @field_validator("status_url")
    @classmethod
    def _check_url(cls, v):
        v = clean(v)
        if v and not str(v).startswith(("http://", "https://")):
            raise ValueError("status_url must start with http:// or https://")
        return v
 
    @field_validator("target_name", "access_log", "error_log", "user_name")
    @classmethod
    def _strip_placeholder(cls, v):
        return clean(v)
 
 
class UpdateWebConfigRequest(BaseModel):
    target_name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    status_url: Optional[str] = None
    access_log: Optional[str] = None
    error_log: Optional[str] = None
    tls_hosts: Optional[List[str]] = None
    user_name: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
 
 
class WebConfigData(BaseModel):
    """Safe view of a stored row — the password is never included."""
    id: int
    agent_name: Optional[str] = None
    server: str
    target_name: Optional[str] = None
    host: str
    port: Optional[int] = None
    status_url: Optional[str] = None
    access_log: Optional[str] = None
    error_log: Optional[str] = None
    tls_hosts: List[str] = []
    user_name: Optional[str] = None
    has_password: bool = False
    is_active: bool = True
 
 
class AddWebConfigResponse(BaseModel):
    created: bool
    web_config: WebConfigData
 
 
class GetWebConfigsResponse(BaseModel):
    total: int
    web_configs: List[WebConfigData]
class FlyStartRequest(BaseModel):
    agent_name: str
    backend: Optional[str] = None          # "api" for remote; None/"cli" => local
    token: Optional[str] = None            # REQUIRED for api backend, from BODY only
    prefers: Optional[str] = None
    org: Optional[str] = None
    apps: Optional[List[str]] = None
    container: Optional[str] = None        # for cli-docker
    # opt-in extras (what to collect)
    want_status: Optional[bool] = None
    want_releases: Optional[bool] = None
    want_checks: Optional[bool] = None
    want_volumes: Optional[bool] = None
    want_ips: Optional[bool] = None
    want_scale: Optional[bool] = None
    want_certs: Optional[bool] = None
    want_metrics: Optional[bool] = None

class AppServerStartRequest(BaseModel):
    agent_name: str
    server: Optional[str] = "wildfly"      # wildfly | jboss
    backend: Optional[str] = "api"         # "api" (HTTP :9990, local/remote) | "cli" (local)
    host: Optional[str] = "127.0.0.1"             # remote IP for remote api; default 127.0.0.1
    port: Optional[int] = 9990             # default 9990
    user: Optional[str] = None             # mgmt user (api backend, from BODY)
    password: Optional[str] = None         # mgmt password (from BODY, never .env)
    cli_path: Optional[str] = None         # jboss-cli path (cli backend; auto-found if omitted)
    controller: Optional[str] = None       # host:port for cli remote (optional)
    want_datasources: Optional[bool] = None
 