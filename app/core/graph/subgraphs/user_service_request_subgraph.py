"""
subgraphs/user_service_request_subgraph.py
-------------------------------------------
Stub subgraph for User Service Request issues on iGOT Karmayogi.

Categories handled (from CATEGORY_SUBCATEGORY_MAP → user_service_request):
  - Request for account activation/deactivation
  - Request to transfer to another department/organization
  - Request to update email address
  - Request to update mobile number
  - Request to update designation
  - Request to update group
  - Request to update eHRMS details
  - Request to assign role
  - Request to add designation
  - Request to add or update service details

STATUS: [STUB] — Full SOP implementation pending.
Action : Creates a support ticket and routes to human specialist.
"""

import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.stub_tools import get_stub_tools
from app.core.utils.prompt_templates import STUB_SUBGRAPH_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class UserServiceRequestSubgraph(BaseSubgraph):

    CATEGORY = "user_service_request"

    def system_prompt(self, state: TicketState) -> str:
        return STUB_SUBGRAPH_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "user_service_request"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_stub_tools()


# Singleton — compiled once at import time
user_service_request_subgraph = UserServiceRequestSubgraph().build()
