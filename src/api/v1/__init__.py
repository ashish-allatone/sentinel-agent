from fastapi import APIRouter
from .auth_api import auth_router
from .agent_management_api import agent_management_router
from .agent_visualisation_api import agent_visualisation_router
from .agent_report_api import agent_report_router
from .communication_api import communication_router
from .soc2_fleet_pdf_api import soc2_fleet_pdf_router
from .communication_send_api import communication_send_router
from .channel_account_api import channel_account_router

v1_api_router = APIRouter(prefix = "/v1")

v1_api_router.include_router(channel_account_router, tags=["channel accounts"])
v1_api_router.include_router(communication_send_router, prefix="/communication", tags=["communication"])
v1_api_router.include_router(soc2_fleet_pdf_router, prefix="/soc2-report", tags=["soc2 pdf"])
v1_api_router.include_router(auth_router, tags = ["Auth router"])
v1_api_router.include_router(agent_management_router, tags = ["Agent Management router"])
v1_api_router.include_router(agent_visualisation_router, tags = ["Agent Visualisation router"])
v1_api_router.include_router(agent_report_router, tags = ["Agent Reports router"])
v1_api_router.include_router(communication_router, tags = ["Communication router"])