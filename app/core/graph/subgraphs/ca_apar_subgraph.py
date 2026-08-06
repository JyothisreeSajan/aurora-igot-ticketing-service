"""
subgraphs/ca_apar_subgraph.py
------------------------------
Stub subgraph for CA/APAR (Comprehensive Assessment / Annual Performance Appraisal
Report) issues on iGOT Karmayogi.

Categories handled (from CATEGORY_SUBCATEGORY_MAP → ca_apar_issue):
  - APAR training plan is not visible
  - Comprehensive assessment program is not visible
  - Course completed, but final assessment is locked
  - Training plan data is not showing in SPARROW APAR

STATUS: [STUB] — Full SOP implementation pending.
Action : Creates a support ticket and routes to human specialist.
"""

import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.stub_tools import get_stub_tools
from app.core.utils.prompt_templates import STUB_SUBGRAPH_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class CaAparSubgraph(BaseSubgraph):

    CATEGORY = "ca_apar_issue"

    def system_prompt(self, state: TicketState) -> str:
        return STUB_SUBGRAPH_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "ca_apar_issue"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_stub_tools()


# Singleton — compiled once at import time
ca_apar_subgraph = CaAparSubgraph().build()
