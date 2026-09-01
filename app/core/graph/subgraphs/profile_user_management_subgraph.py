"""
subgraphs/profile_user_management_subgraph.py
------------------------------------------------
Stub subgraph for Profile & User Management issues on iGOT Karmayogi.

Categories handled (from CATEGORY_SUBCATEGORY_MAP → profile_and_user_management):
  - Access Revoked
  - Email / Mobile already registered
  - Profile Verification / Verified Badge
  - Designation / Group Not verified
  - Profile Update

STATUS: [STUB] — Full SOP implementation pending.
Action : Creates a support ticket and routes to human specialist.
"""

import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.stub_tools import get_stub_tools
from app.core.utils.prompt_templates import STUB_SUBGRAPH_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class ProfileUserManagementSubgraph(BaseSubgraph):

    CATEGORY = "profile_and_user_management"

    def system_prompt(self, state: TicketState) -> str:
        return STUB_SUBGRAPH_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "profile_and_user_management"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_stub_tools()


# Singleton — compiled once at import time
profile_user_management_subgraph = ProfileUserManagementSubgraph().build()
