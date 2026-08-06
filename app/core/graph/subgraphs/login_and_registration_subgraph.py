"""
subgraphs/login_and_registration_subgraph.py
---------------------------------------------
Specialist subgraph for login and registration-related SOP tickets
on iGOT Karmayogi.

Handles:
  - New user registration issues
  - Login failures (credentials, OTP, session)
  - Account activation and verification
  - Password reset and recovery
  - First-time login setup

Tools available:
  - validate_email_domain       : check whether an email domain is whitelisted
  - get_user_profile            : fetch user profile data
  - get_mdo_details             : fetch MDO details
  - get_yp_am_details           : fetch YP/AM details
  - lookup_user_by_contact      : check registered user
  - get_user_enrollments        : check user courses
  - get_user_transfer_request   : check transfer request status
"""

import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.login_issue_tool import get_login_tools
from app.core.utils.prompt_templates import LOGIN_REGISTRATION_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class LoginAndRegistrationSubgraph(BaseSubgraph):

    CATEGORY = "login_and_registration"

    def system_prompt(self, state: TicketState) -> str:
        return LOGIN_REGISTRATION_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "login_and_registration"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_login_tools()


# Singleton — compiled once at import time
login_and_registration_subgraph = LoginAndRegistrationSubgraph().build()
