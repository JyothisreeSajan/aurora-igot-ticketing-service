"""
subgraphs/content_related_subgraph.py
-----------------------------------------
Specialist subgraph for Content Related Issues on iGOT Karmayogi.

Categories handled (from CATEGORY_SUBCATEGORY_MAP -> content_related_issue):
  - Enrolment Issues                        [implemented — unable to find/enroll
                                              in a course, program, moderated
                                              course, or event; see
                                              ENROLMENT_ISSUES_SYSTEM_PROMPT]
  - Course / Program Progress Issue         [stub]
  - Content / Resource Not Opening          [stub]
  - Event Related Issue                     [stub]
  - Certificate Issue                       [stub]
  - Unable to submit rating/feedback        [stub]

Stub sub-categories still create a support ticket and route to a human
specialist via STUB_SUBGRAPH_SYSTEM_PROMPT, same as before.
"""

import json
import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.enrolment_tools import get_enrolment_tools
from app.core.tools.stub_tools import get_stub_tools
from app.core.utils.prompt_templates import (
    ENROLMENT_ISSUES_SYSTEM_PROMPT,
    STUB_SUBGRAPH_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

_IMPLEMENTED_SUB_CATEGORIES = {"enrolment_issues"}


class ContentRelatedSubgraph(BaseSubgraph):

    CATEGORY = "content_related_issue"

    def _is_implemented(self, state: TicketState) -> bool:
        return state.get("sub_category") in _IMPLEMENTED_SUB_CATEGORIES

    def system_prompt(self, state: TicketState) -> str:
        if self._is_implemented(state):
            return ENROLMENT_ISSUES_SYSTEM_PROMPT.format(
                email=state.get("email", "unknown"),
                main_category=state.get("main_category", "content_related_issue"),
            )
        return STUB_SUBGRAPH_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "content_related_issue"),
        )

    def get_tools(self, state: TicketState) -> list:
        if self._is_implemented(state):
            return get_enrolment_tools()
        return get_stub_tools()

    # ── Greeting name fix — same pattern as the other real subgraphs ─────────

    def execute_node(self, state: TicketState) -> TicketState:
        result = super().execute_node(state)
        first_name = self._extract_first_name(result.get("tool_results") or [])
        if first_name:
            result = {**result, "user_first_name": first_name}
        return result

    def _extract_first_name(self, tool_results: list) -> str | None:
        for r in reversed(tool_results):
            if r.get("tool") != "get_user_eligibility_profile":
                continue
            try:
                data = json.loads(r["summary"])
            except Exception:
                continue
            first_name = data.get("first_name")
            if first_name:
                return first_name
        return None


# Singleton — compiled once at import time
content_related_subgraph = ContentRelatedSubgraph().build()
