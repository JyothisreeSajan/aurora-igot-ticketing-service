"""
subgraphs/profile_update_subgraph.py
--------------------------------------
Specialist subgraph for profile update and leaderboard issues on iGOT Karmayogi.

SOP workflows handled (from Agent_SOP_Profile_Update.md):
  SOP-P1: Profile verification / Designation or Group not verified /
           Verified Community Badge not visible
  SOP-P2: Leaderboard / Top Karmayogi Dashboard not displayed or not updated

All tools are sourced from app.core.tools.profile_update_tool.
The full SOP is embedded in PROFILE_UPDATE_SYSTEM_PROMPT — no KB lookup required.
"""

import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.profile_update_tool import get_profile_update_tools
from app.core.utils.prompt_templates import PROFILE_UPDATE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class ProfileUpdateSubgraph(BaseSubgraph):

    CATEGORY = "profile_update"

    def system_prompt(self, state: TicketState) -> str:
        return PROFILE_UPDATE_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "profile_update"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_profile_update_tools()


# Singleton — compiled once at import time
profile_update_subgraph = ProfileUpdateSubgraph().build()
