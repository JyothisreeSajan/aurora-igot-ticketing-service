"""
subgraphs/content_related_subgraph.py
-----------------------------------------
Stub subgraph for Content Related Issues on iGOT Karmayogi.

Categories handled (from CATEGORY_SUBCATEGORY_MAP → content_related_issue):
  - Enrolment Issues
  - Course / Program Progress Issue
  - Content / Resource Not Opening
  - Event Related Issue
  - Certificate Issue
  - Unable to submit rating/feedback

STATUS: [STUB] — Full SOP implementation pending.
Action : Creates a support ticket and routes to human specialist.
"""

import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.stub_tools import get_stub_tools
from app.core.utils.prompt_templates import STUB_SUBGRAPH_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class ContentRelatedSubgraph(BaseSubgraph):

    CATEGORY = "content_related_issue"

    def system_prompt(self, state: TicketState) -> str:
        return STUB_SUBGRAPH_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "content_related_issue"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_stub_tools()


# Singleton — compiled once at import time
content_related_subgraph = ContentRelatedSubgraph().build()
