"""
subgraphs/organisation_subgraph.py
------------------------------------
Stub subgraph for Organisation Request issues on iGOT Karmayogi.

Categories handled (from CATEGORY_SUBCATEGORY_MAP → organisation_request):
  - Request to add domain
  - Request to Create MDO Channel
  - Request to Create ATI/CTI Page
  - Request to Delete Organisation

STATUS: [STUB] — Full SOP implementation pending.
Action : Creates a support ticket and routes to human specialist.
"""

import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.stub_tools import get_stub_tools
from app.core.utils.prompt_templates import STUB_SUBGRAPH_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class OrganisationSubgraph(BaseSubgraph):

    CATEGORY = "organisation_request"

    def system_prompt(self, state: TicketState) -> str:
        return STUB_SUBGRAPH_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "organisation_request"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_stub_tools()


# Singleton — compiled once at import time
organisation_subgraph = OrganisationSubgraph().build()
