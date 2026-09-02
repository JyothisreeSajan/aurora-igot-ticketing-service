"""
subgraphs/recognition_engagement_subgraph.py
----------------------------------------------
Specialist subgraph for recognition & engagement issues on iGOT Karmayogi.

SOP workflows handled (from Agent_SOP_Recognition_Engagement.md):
  SOP-RE1: Karma Points Issue
  SOP-RE2: Weekly Claps Issue
  SOP-RE3: Learning Hours Issue - eHRMS
  SOP-RE4: Learning Hours Issue - Shiksha Path
  SOP-RE5: Learning Hours Issue - SPARROW / APAR
  SOP-RE6: Leader Board Issue

All tools are sourced from app.core.tools.recognition_engagement_tools.
The full SOP is embedded in RECOGNITION_ENGAGEMENT_SYSTEM_PROMPT — no KB lookup required.
"""

import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.recognition_engagement_tools import get_recognition_engagement_tools
from app.core.utils.prompt_templates import RECOGNITION_ENGAGEMENT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class RecognitionEngagementSubgraph(BaseSubgraph):

    CATEGORY = "recognition_and_engagement"

    def system_prompt(self, state: TicketState) -> str:
        return RECOGNITION_ENGAGEMENT_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "recognition_and_engagement"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_recognition_engagement_tools()


# Singleton — compiled once at import time
recognition_engagement_subgraph = RecognitionEngagementSubgraph().build()
