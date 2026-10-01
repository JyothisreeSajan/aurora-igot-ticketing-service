"""
subgraphs/content_related_subgraph.py
-----------------------------------------
Specialist subgraph for Content Related Issues on iGOT Karmayogi.

Categories handled (from CATEGORY_SUBCATEGORY_MAP -> content_related_issue):
  - Enrolment Issues                        [implemented — SOP 1: unable to find/enroll
                                              in a course, program, moderated course, or
                                              event; SOP 2: request to unenroll/withdraw
                                              from an already-enrolled course, program, or
                                              event; see ENROLMENT_ISSUES_SYSTEM_PROMPT]
  - Course / Program Progress Issue         [implemented — progress not updating,
                                              for courses/programs (events out of
                                              scope); certificate-not-generated is
                                              NOT handled here — see Certificate Not
                                              Received below; see
                                              COURSE_PROGRESS_SYSTEM_PROMPT]
  - Content / Resource Not Opening          [stub]
  - Event Related Issue                     [implemented — event video missing / not
                                              playing / progress not updating (event
                                              certificate: see Certificate Not Received); see
                                              EVENT_ISSUES_SYSTEM_PROMPT]
  - Certificate Issue                       [implemented — Incorrect Name on Certificate:
                                              fetches the profile's on-file name, guides a
                                              re-download, and if still wrong, guides a
                                              profile-name update + re-download; see
                                              CERTIFICATE_NAME_ISSUE_SYSTEM_PROMPT]
  - Certificate Not Received / Generated    [implemented — UC-03, courses, programs and
                                              events: certificate not received/generated; guides
                                              download once available (issued, or completed
                                              >24h ago), or asks the user to wait if within
                                              24h of completion; see
                                              CERTIFICATE_NOT_RECEIVED_SYSTEM_PROMPT]
  - Unable to submit rating/feedback        [implemented — no tool call; explains progress-
                                              update delay and that rating isn't required for
                                              certificate generation; see
                                              RATING_FEEDBACK_ISSUE_SYSTEM_PROMPT]

Stub sub-categories still create a support ticket and route to a human
specialist via STUB_SUBGRAPH_SYSTEM_PROMPT, same as before.
"""

import json
import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.certificate_tools import get_certificate_not_received_tools, get_user_details
from app.core.tools.course_progress_tools import get_course_progress_tools
from app.core.tools.enrolment_tools import get_enrolment_tools
from app.core.tools.event_tools import get_event_tools
from app.core.tools.stub_tools import get_stub_tools
from app.core.utils.prompt_templates import (
    CERTIFICATE_NAME_ISSUE_SYSTEM_PROMPT,
    CERTIFICATE_NOT_RECEIVED_SYSTEM_PROMPT,
    COURSE_PROGRESS_SYSTEM_PROMPT,
    ENROLMENT_ISSUES_SYSTEM_PROMPT,
    EVENT_ISSUES_SYSTEM_PROMPT,
    RATING_FEEDBACK_ISSUE_SYSTEM_PROMPT,
    STUB_SUBGRAPH_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

# Each implemented sub-category maps to its own (prompt, tools-getter) pair.
# Any sub-category not listed here falls through to the generic stub.
_SUB_CATEGORY_PROMPTS: dict[str, str] = {
    "enrolment_issues": ENROLMENT_ISSUES_SYSTEM_PROMPT,
    "course_program_progress_issue": COURSE_PROGRESS_SYSTEM_PROMPT,
    "event_related_issue": EVENT_ISSUES_SYSTEM_PROMPT,
    "unable_to_submit_rating_feedback": RATING_FEEDBACK_ISSUE_SYSTEM_PROMPT,
    "certificate_issue": CERTIFICATE_NAME_ISSUE_SYSTEM_PROMPT,
    "certificate_not_received": CERTIFICATE_NOT_RECEIVED_SYSTEM_PROMPT,
}
_SUB_CATEGORY_TOOLS = {
    "enrolment_issues": get_enrolment_tools,
    "course_program_progress_issue": get_course_progress_tools,
    "event_related_issue": get_event_tools,
    "unable_to_submit_rating_feedback": lambda: [],  # no tool call needed for this SOP
    "certificate_issue": lambda: [get_user_details],
    "certificate_not_received": get_certificate_not_received_tools,
}


class ContentRelatedSubgraph(BaseSubgraph):

    CATEGORY = "content_related_issue"

    def _is_implemented(self, state: TicketState) -> bool:
        return state.get("sub_category") in _SUB_CATEGORY_PROMPTS

    def system_prompt(self, state: TicketState) -> str:
        sub_category = state.get("sub_category")
        prompt = _SUB_CATEGORY_PROMPTS.get(sub_category, STUB_SUBGRAPH_SYSTEM_PROMPT)
        return prompt.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "content_related_issue"),
        )

    def get_tools(self, state: TicketState) -> list:
        tools_fn = _SUB_CATEGORY_TOOLS.get(state.get("sub_category"))
        if tools_fn is None:
            return get_stub_tools()
        return tools_fn()

    # ── Greeting name fix — same pattern as the other real subgraphs ─────────

    def execute_node(self, state: TicketState) -> TicketState:
        result = super().execute_node(state)
        first_name = self._extract_first_name(result.get("tool_results") or [])
        if first_name:
            result = {**result, "user_first_name": first_name}
        return result

    _NAME_SOURCE_TOOLS = ("get_user_eligibility_profile", "get_user_details")

    def _extract_first_name(self, tool_results: list) -> str | None:
        for r in reversed(tool_results):
            if r.get("tool") not in self._NAME_SOURCE_TOOLS:
                continue
            try:
                data = json.loads(r["summary"])
            except Exception:
                continue
            first_name = data.get("first_name") or data.get("firstName")
            if first_name:
                return first_name
        return None


# Singleton — compiled once at import time
content_related_subgraph = ContentRelatedSubgraph().build()
