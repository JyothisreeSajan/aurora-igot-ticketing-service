"""
subgraphs/recognition_engagement_subgraph.py
-----------------------------------------------
Stub subgraph for Recognition & Engagement issues on iGOT Karmayogi.

Categories handled (from CATEGORY_SUBCATEGORY_MAP → recognition_and_engagement):
  - Karma Points Issue
  - Weekly Claps Issue
  - Leader Board Issue

STATUS: [STUB] — Full SOP implementation pending.
Action : Creates a support ticket and routes to human specialist.
"""

import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.stub_tools import get_stub_tools
from app.core.utils.prompt_templates import STUB_SUBGRAPH_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class RecognitionEngagementSubgraph(BaseSubgraph):

    CATEGORY = "recognition_and_engagement"

    def system_prompt(self, state: TicketState) -> str:
        return STUB_SUBGRAPH_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "recognition_and_engagement"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_stub_tools()


# Singleton — compiled once at import time
recognition_engagement_subgraph = RecognitionEngagementSubgraph().build()
