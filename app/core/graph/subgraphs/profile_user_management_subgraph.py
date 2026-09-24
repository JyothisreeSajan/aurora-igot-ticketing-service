"""
subgraphs/profile_user_management_subgraph.py
------------------------------------------------
Specialist subgraph for Profile & User Management issues on iGOT Karmayogi.

Categories handled (from CATEGORY_SUBCATEGORY_MAP → profile_and_user_management):
  - Access Revoked                          [SOP-A1 — implemented, both transfer-already-
                                              raised and no-transfer-raised-yet cases]
  - Email / Mobile already registered       [SOP-A2 — implemented: domain check, duplicate-
                                              registration check, and confirm-then-escalate
                                              flow for accounts already linked elsewhere]
  - Profile Verification / Verified Badge   [not yet implemented — escalates]
  - Designation / Group Not verified        [not yet implemented — escalates]
  - Profile Update                          [SOP-P1..P12 implemented — Name Update, Display
                                              Name Update, Designation Not Found, Email/
                                              Mobile OTP Not Received, Mother Tongue Update,
                                              EHRMS ID Update, Date of Retirement Update,
                                              Service History Update, Educational
                                              Qualification Update, Profile Photo Update,
                                              Cover Photo Update, Profile Completion Not
                                              Showing 100%]
  - Profile Verification / Verified Badge   [SOP-A3 implemented — same flow as
  - Designation / Group Not verified          Designation/Group below, wording branches on
                                              whether the ticket message says "badge" or
                                              "designation/group"]
  - Profile Update                          [SOP-P1/P2/P3/P4 implemented — Name Update,
                                              Display Name Update, Designation Not Found,
                                              Email/Mobile OTP Not Received]

All tools are sourced from app.core.tools.profile_user_management_tools.
The full SOP is embedded in PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT — no KB lookup required.
"""

import json
import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.profile_user_management_tools import (
    get_profile_user_management_tools,
)
from app.core.utils.prompt_templates import PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class ProfileUserManagementSubgraph(BaseSubgraph):

    CATEGORY = "profile_and_user_management"

    def system_prompt(self, state: TicketState) -> str:
        return PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "profile_and_user_management"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_profile_user_management_tools()

    # ── Greeting name fix ────────────────────────────────────────────────────
    #
    # Same fix as CaAparSubgraph/RecognitionEngagementSubgraph: the email
    # greeting falls back to "there" when intake's own name lookup doesn't
    # surface the real first name. get_user_transfer_request_details (SOP-A1)
    # and get_user_profile (SOP-A2, aliased get_own_profile_details) both
    # already return firstName for the ticket owner's own account.

    _NAME_SOURCE_TOOLS = ("get_user_transfer_request_details", "get_user_profile")

    def execute_node(self, state: TicketState) -> TicketState:
        result = super().execute_node(state)
        first_name = self._extract_first_name(result.get("tool_results") or [])
        if first_name:
            result = {**result, "user_first_name": first_name}
        return result

    def _extract_first_name(self, tool_results: list) -> str | None:
        for r in reversed(tool_results):
            if r.get("tool") not in self._NAME_SOURCE_TOOLS:
                continue
            try:
                data = json.loads(r["summary"])
            except Exception:
                continue
            first_name = data.get("firstName") or data.get("first_name")
            if first_name:
                return first_name
        return None

    # ── Genuine dead-end -> real human hand-off, no automated email ──────────
    #
    # SOP-A1's "neither MDO nor YP/SPOC found" dead end (STEP 3 and Edge Case 2)
    # should reach a human agent with no automated email sent, unlike every
    # other escalate=true case in this codebase (which still sends a full
    # email). Reuses the ALREADY-EXISTING low-confidence gate in router_node —
    # no changes needed to main_graph.py or router_node.py: leaving the draft
    # empty makes quality_gate fail once (empty final_response), which sends
    # the ticket back to router_node; setting confidence low there makes
    # router_node's existing confidence check route it to human_queue directly.
    # Detected structurally — escalated_to_human=True AND the last
    # get_yp_am_details call came back found=false — not by matching reason text.

    def decide_node(self, state: TicketState) -> TicketState:
        result = super().decide_node(state)
        if not result.get("escalated_to_human"):
            return result

        tool_results = result.get("tool_results") or []
        dead_end_reason = None
        if self._tool_lookup_failed(tool_results, "get_yp_am_details"):
            # get_yp_am_details is the final fallback for two flows: Access
            # Revoked (STEP 3 / Edge Case 2) and Date of Retirement Update
            # (Case 2's MDO->YP chain). Distinguished by which flow-specific
            # tool was ALSO called this turn — get_user_transfer_request_details
            # only appears in Access Revoked, get_user_ehrms_details only in
            # Date of Retirement Update.
            if any(r.get("tool") == "get_user_ehrms_details" for r in tool_results):
                dead_end_reason = "Date of Retirement Update: no MDO Admin or YP/SPOC contact found for the user's organisation."
            else:
                dead_end_reason = "Access Revoked: no MDO Admin or YP/SPOC contact found for the target organization."
        elif self._tool_lookup_failed(tool_results, "check_mother_tongue_available"):
            dead_end_reason = "Mother Tongue Update: reported mother tongue not found in the platform's master data."
        elif (
            # get_mdo_details_by_org_id is shared by four flows: Designation
            # Not Found (Case 2), OTP Not Received, EHRMS ID Update, and
            # Service History Update. Only the latter three get the silent
            # hand-off when no MDO is found — Designation Not Found always
            # ALSO calls get_org_imported_designations first, which the other
            # three never do, so checking for its absence here scopes this to
            # exactly those three flows and no others.
            not any(r.get("tool") == "get_org_imported_designations" for r in tool_results)
            and self._tool_lookup_failed(tool_results, "get_mdo_details_by_org_id")
        ):
            dead_end_reason = "No MDO Admin found for the target organisation."

        if dead_end_reason:
            logger.info(
                f"[{self.CATEGORY}] Genuine dead end ({dead_end_reason}) — no automated "
                f"email; routing to human_queue via the existing low-confidence gate."
            )
            result = {
                **result,
                "resolution_draft": "",
                "confidence": 0.0,
                "escalation_reason": dead_end_reason,
            }
        return result

    def _tool_lookup_failed(self, tool_results: list, tool_name: str) -> bool:
        """True if the LAST call to tool_name in this turn returned found=false."""
        for r in reversed(tool_results):
            if r.get("tool") != tool_name:
                continue
            try:
                data = json.loads(r["summary"])
            except Exception:
                continue
            return not data.get("found", False)
        return False


# Singleton — compiled once at import time
profile_user_management_subgraph = ProfileUserManagementSubgraph().build()