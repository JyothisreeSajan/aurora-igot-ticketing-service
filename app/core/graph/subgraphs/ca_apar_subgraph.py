"""
subgraphs/ca_apar_subgraph.py
------------------------------
Specialist subgraph for CA/APAR (Comprehensive Assessment / Annual Performance
Appraisal Report) issues on iGOT Karmayogi.

Categories handled (from CATEGORY_SUBCATEGORY_MAP → ca_apar_issue):
  - APAR / Training Plan Not Visible                       [SOP-1 — implemented]
  - APAR / Training Plan Unexpected courses visible         [SOP-1, Edge Case 1]
  - APAR / Training Plan - Incorrect Plan Assigned          [SOP-1, Edge Case 2]
  - Comprehensive Assessment Program (CAP) - Final
    Assessment Locked                                       [SOP-3 — implemented,
    including the verified assessment-attempt-limit check (STEP 5B, via
    get_assessment_attempt_count, targeting the CAP's actual "Course Assessment"
    child when get_cap_hierarchy finds one) and STEP 3/4's SCORM/Non-SCORM/
    Assessment classification of each child resource; gated by STEP 0, which
    asks the user to name the CAP/course before any tool runs if their ticket
    message didn't already name one; the child-course technical-issue/ticket
    branch and CAP hyperlink sharing are intentionally not implemented —
    pending confirmation of the ticketing mechanism and link-generation source]

All tools are sourced from app.core.tools.ca_apar_tool.
The full SOP is embedded in CA_APAR_SYSTEM_PROMPT — no KB lookup required.
"""

import json
import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.ca_apar_tool import get_ca_apar_tools
from app.core.utils.prompt_templates import CA_APAR_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class CaAparSubgraph(BaseSubgraph):

    CATEGORY = "ca_apar_issue"

    def system_prompt(self, state: TicketState) -> str:
        return CA_APAR_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "ca_apar_issue"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_ca_apar_tools()

    # ── Greeting name fix ────────────────────────────────────────────────────
    #
    # intake_node's own name lookup (a separate, shared mechanism) doesn't
    # always surface the real first name, so the email greeting falls back to
    # "there". get_user_cbp_plan runs in STEP 1 for every CA/APAR ticket
    # (before any branching) and already fetches the profile internally to
    # get the user ID, so it — and get_user_profile, when that also runs —
    # reliably return firstName for the same account. Checking both covers
    # every branch, not just the ones that call get_user_profile. Exception:
    # SOP-3's STEP 0 can hold off calling any tool on the very first turn
    # (when the user hasn't named a CAP/course yet) — the name simply isn't
    # available until a later turn's tool call surfaces it.

    _NAME_SOURCE_TOOLS = ("get_user_cbp_plan", "get_assigned_cap_courses", "get_user_profile")

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
                profile = json.loads(r["summary"])
            except Exception:
                continue
            first_name = profile.get("firstName") or profile.get("first_name")
            if first_name:
                return first_name
        return None


# Singleton — compiled once at import time
ca_apar_subgraph = CaAparSubgraph().build()
