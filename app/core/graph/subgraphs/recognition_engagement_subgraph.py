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

import json
import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.recognition_engagement_tools import get_recognition_engagement_tools
from app.core.utils.prompt_templates import (
    RECOGNITION_ENGAGEMENT_SOP_PROMPTS,
    RECOGNITION_ENGAGEMENT_SYSTEM_PROMPT,
)
from app.core.utils.recognition_signals import resolve_sop

logger = logging.getLogger(__name__)


class RecognitionEngagementSubgraph(BaseSubgraph):

    CATEGORY = "recognition_and_engagement"

    @staticmethod
    def _ticket_text(state: TicketState) -> str:
        """All user-written text on the ticket (the whole thread on a continuation)."""
        if state.get("is_continuation"):
            msgs = [m.get("content", "") for m in (state.get("conversation_messages") or [])
                    if m.get("role") == "user"]
            if msgs:
                return "\n".join(msgs)
        return state.get("message", "") or ""

    def system_prompt(self, state: TicketState) -> str:
        # Code (not the model) picks the single SOP to follow; falls back to the
        # full six-SOP prompt only when the text gives no usable signal.
        sop = resolve_sop(state.get("sub_category", ""), self._ticket_text(state))
        template = RECOGNITION_ENGAGEMENT_SOP_PROMPTS.get(sop, RECOGNITION_ENGAGEMENT_SYSTEM_PROMPT)
        prompt = template.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "recognition_and_engagement"),
        )
        if sop in RECOGNITION_ENGAGEMENT_SOP_PROMPTS:
            prompt += (
                f"\n\nACTIVE SOP (selected by code from the ticket text — do not switch): "
                f"SOP-{sop}. Follow ONLY this SOP and ignore the others. Never ask the user "
                "which portal or topic this is about unless this SOP itself says to."
            )
        logger.info(f"[recognition_engagement] ticket={state.get('ticket_id')} "
                    f"sub_category='{state.get('sub_category')}' -> sop={sop}")
        return prompt

    def get_tools(self, state: TicketState) -> list:
        return get_recognition_engagement_tools()

    # ── Greeting name fix ────────────────────────────────────────────────────
    #
    # Same fix as CaAparSubgraph: the email greeting sometimes falls back to
    # "there" because the name lookup at intake fails. Fix it here by grabbing
    # the first name from a tool result we already have, instead of the user.

    _NAME_SOURCE_TOOLS = ("get_user_first_name", "get_user_profile")

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


# Singleton — compiled once at import time
recognition_engagement_subgraph = RecognitionEngagementSubgraph().build()
