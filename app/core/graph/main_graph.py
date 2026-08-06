"""
main_graph.py
-------------
iGOT Karmayogi Resolution Graph — top-level LangGraph orchestrator.

User input   : { email, message }
Graph output : { final_response, category, main_category, ... }

Flow:
    intake_node (includes junk detection)
        ↓ (conditional)
    router_node  /  notify_user (if junk)
        ↓ (conditional)
    ┌──────────────────────────────────────────────────────────┐
    │  Fully Implemented                                       │
    │    certificate_subgraph                                  │
    │    courses_subgraph               → promote_draft        │
    │    login_and_registration_subgraph  → quality_gate       │
    │    profile_update_subgraph                               │
    │  Stubs (escalate to support ticket)                      │
    │    ca_apar_subgraph                                      │
    │    organisation_subgraph                                 │
    │    user_service_request_subgraph                         │
    │    general_query_subgraph                                │
    │    mobile_application_subgraph                           │
    │    virtual_event_subgraph                                │
    │    program_subgraph                                      │
    └──────────────────────────────────────────────────────────┘
        ↓ (conditional from quality_gate)
    notify_user  /  router_node (re-route)  /  human_queue
        ↓
       END

Public API
----------
    from app.core.graph.main_graph import run_ticket, arun_ticket

    result = run_ticket({"email": "user@gov.in", "message": "My certificate is missing."})
    result = await arun_ticket({"email": "user@gov.in", "message": "Cannot login to iGOT."})
"""

import json
import logging
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph

from app.core.graph.nodes.intake_node import intake_node
from app.core.graph.nodes.router_node import route_decision, router_node
from app.core.graph.state import TicketState

# ── Stub subgraphs (pending full SOP implementation) ─────────────────────────
from app.core.graph.subgraphs.ca_apar_subgraph import ca_apar_subgraph
from app.core.graph.subgraphs.certificate_subgraph import certificate_subgraph
from app.core.graph.subgraphs.courses_subgraph import courses_subgraph
from app.core.graph.subgraphs.general_query_subgraph import general_query_subgraph
from app.core.graph.subgraphs.login_and_registration_subgraph import (
    login_and_registration_subgraph,
)
from app.core.graph.subgraphs.mobile_application_subgraph import (
    mobile_application_subgraph,
)
from app.core.graph.subgraphs.organisation_subgraph import organisation_subgraph
from app.core.graph.subgraphs.profile_update_subgraph import profile_update_subgraph
from app.core.graph.subgraphs.program_subgraph import program_subgraph
from app.core.graph.subgraphs.user_service_request_subgraph import (
    user_service_request_subgraph,
)
from app.core.graph.subgraphs.virtual_event_subgraph import virtual_event_subgraph
from app.core.utils.constants import GraphStage, build_email_html, get_llm_model

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.75
MAX_QUALITY_REROUTES = 2

# LLM-based quality check definitions
QUALITY_GATE_SYSTEM_PROMPT = (
    "You are a Quality Gate Auditor for iGOT Karmayogi support emails. "
    "Your job is to analyze the final email response (HTML format) and detect if there is any "
    "repetition of greetings or salutations.\n\n"
    "Specifically, check if the email addresses the user multiple times. "
    "The standard template already includes a greeting like '<p>Hi {name},</p><p>Greetings from...'. "
    "If the inner body (inside the <div>...</div>) ALSO starts with a greeting or salutation (such as 'Dear Dr. Harshit Pant,', "
    "'Hi Dr. Harshit,', 'Hello,', etc.), then this is a duplicate/repetition and MUST be flagged.\n"
    "Wait: A single greeting at the very beginning of the email (the template's greeting) is correct. "
    "You only flag it if there are multiple greetings/salutations across the whole email.\n\n"
    "Respond ONLY with a valid JSON object (no markdown, no other text):\n"
    "{\n"
    "  \"has_repetition\": true/false,\n"
    "  \"reason\": \"<brief description of the repetition if found, otherwise empty>\"\n"
    "}"
)

QUALITY_GATE_HUMAN_PROMPT = "Analyze this final response:\n---\n{final_response}\n---"

_llm_quality = ChatGoogleGenerativeAI(model=get_llm_model(GraphStage.QUALITY_GATE), temperature=0)

from app.core.utils.ticket_tracker import ticket_tracker


def _plan_step(ticket_id: str, node: str, detail: str, **extra) -> dict:
    """Create a single graph_plan step dict and push to Elasticsearch."""
    from datetime import datetime
    step_dict = {"node": node, "detail": detail, "timestamp": datetime.now().strftime("%H:%M:%S"), **extra}
    if ticket_id and ticket_id != "unknown":
        ticket_tracker.add_step(ticket_id, node, detail, extra)
    return step_dict


def _format_email_response(body: str, first_name: str) -> str:
    """
    Wrap the LLM-generated HTML fragment in the standard customer-facing email template.
    Template is defined in app.core.utils.constants.EMAIL_HTML_TEMPLATE.
    Falls back to 'there' when first_name is empty.
    """
    if not body:
        return body
    name = first_name.strip() if first_name and first_name.strip() else "there"
    return build_email_html(body, name=name)


def after_intake(state: TicketState) -> Literal["router_node", "notify_user"]:
    """Route junk/early-resolved messages directly to notify_user, others to router_node."""
    if state.get("is_junk") or state.get("is_resolved"):
        return "notify_user"
    return "router_node"


# ── Subgraph wrapper nodes ────────────────────────────────────────────────────
# Fully implemented

def run_certificate_subgraph(state: TicketState) -> TicketState:
    tid = state.get("ticket_id", "unknown")
    logger.info(f"[main_graph] certificate_subgraph — ticket={tid}")
    return {**state, **certificate_subgraph.invoke(state)}


def run_courses_subgraph(state: TicketState) -> TicketState:
    tid = state.get("ticket_id", "unknown")
    logger.info(f"[main_graph] courses_subgraph — ticket={tid}")
    return {**state, **courses_subgraph.invoke(state)}


def run_login_and_registration_subgraph(state: TicketState) -> TicketState:
    tid = state.get("ticket_id", "unknown")
    logger.info(f"[main_graph] login_and_registration_subgraph — ticket={tid}")
    return {**state, **login_and_registration_subgraph.invoke(state)}


def run_profile_update_subgraph(state: TicketState) -> TicketState:
    tid = state.get("ticket_id", "unknown")
    logger.info(f"[main_graph] profile_update_subgraph — ticket={tid}")
    return {**state, **profile_update_subgraph.invoke(state)}


# Stubs — escalate to support ticket until SOP is implemented

def run_ca_apar_subgraph(state: TicketState) -> TicketState:
    tid = state.get("ticket_id", "unknown")
    logger.info(f"[main_graph] ca_apar_subgraph (stub) — ticket={tid}")
    return {**state, **ca_apar_subgraph.invoke(state)}


def run_organisation_subgraph(state: TicketState) -> TicketState:
    tid = state.get("ticket_id", "unknown")
    logger.info(f"[main_graph] organisation_subgraph (stub) — ticket={tid}")
    return {**state, **organisation_subgraph.invoke(state)}


def run_user_service_request_subgraph(state: TicketState) -> TicketState:
    tid = state.get("ticket_id", "unknown")
    logger.info(f"[main_graph] user_service_request_subgraph (stub) — ticket={tid}")
    return {**state, **user_service_request_subgraph.invoke(state)}


def run_general_query_subgraph(state: TicketState) -> TicketState:
    tid = state.get("ticket_id", "unknown")
    logger.info(f"[main_graph] general_query_subgraph (stub) — ticket={tid}")
    return {**state, **general_query_subgraph.invoke(state)}


def run_mobile_application_subgraph(state: TicketState) -> TicketState:
    tid = state.get("ticket_id", "unknown")
    logger.info(f"[main_graph] mobile_application_subgraph (stub) — ticket={tid}")
    return {**state, **mobile_application_subgraph.invoke(state)}


def run_virtual_event_subgraph(state: TicketState) -> TicketState:
    tid = state.get("ticket_id", "unknown")
    logger.info(f"[main_graph] virtual_event_subgraph (stub) — ticket={tid}")
    return {**state, **virtual_event_subgraph.invoke(state)}


def run_program_subgraph(state: TicketState) -> TicketState:
    tid = state.get("ticket_id", "unknown")
    logger.info(f"[main_graph] program_subgraph (stub) — ticket={tid}")
    return {**state, **program_subgraph.invoke(state)}


# ── Utility nodes ─────────────────────────────────────────────────────────────

def promote_draft_to_final(state: TicketState) -> TicketState:
    """Move resolution_draft → final_response, wrapped in the email template."""
    draft      = state.get("resolution_draft", "")
    first_name = state.get("user_first_name", "")
    formatted  = _format_email_response(draft, first_name)
    return {**state, "final_response": formatted}


def quality_gate_node(state: TicketState) -> TicketState:
    """Checks that a non-empty final_response was produced.
    Also calls LLM to check if there is greeting/salutation repetition.
    A clarification question is a valid response and passes the gate.
    """
    final_response      = state.get("final_response", "").strip()
    is_resolved         = state.get("is_resolved", False)
    needs_clarification = state.get("needs_clarification", False)
    issues = []

    if not final_response:
        issues.append("final_response is empty.")

    # LLM-based duplicate greeting check
    quality_gate_feedback = None
    if final_response and not needs_clarification:
        try:
            from app.core.utils.helpers import mask_pii_default
            messages = [
                SystemMessage(content=QUALITY_GATE_SYSTEM_PROMPT),
                HumanMessage(content=QUALITY_GATE_HUMAN_PROMPT.format(final_response=mask_pii_default(final_response)))
            ]
            response = _llm_quality.invoke(messages)
            raw = response.content.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            parsed = json.loads(raw)
            if parsed.get("has_repetition"):
                reason = parsed.get("reason", "Duplicate greeting/salutation detected.")
                issues.append(f"Greeting repetition check failed: {reason}")
                quality_gate_feedback = (
                    "The final response contains a duplicate greeting or salutation at the start of the draft body. "
                    "Please rewrite the response body starting directly with the resolution content "
                    "without repeating greetings (e.g., do not start with 'Dear Dr. Harshit Pant,' or 'Hi...')."
                )
        except Exception as e:
            logger.error(f"[quality_gate] LLM check error: {e}")
            # Do not block execution if LLM fails, just log it

    # Only flag as unresolved when it's neither resolved NOR a clarification
    if not is_resolved and not needs_clarification and state.get("retry_count", 0) >= state.get("max_retries", 3):
        issues.append("Ticket unresolved after max retries.")

    passed        = len(issues) == 0
    reroute_count = state.get("quality_reroute_count", 0)
    logger.info(f"[quality_gate] ticket={state.get('ticket_id')} passed={passed} "
                f"needs_clarification={needs_clarification} issues={issues}")

    step = _plan_step(
        state.get("ticket_id", "unknown"),
        "quality_gate",
        f"Gate {'PASSED' if passed else 'FAILED'}. needs_clarification={needs_clarification}. Issues: {issues or 'none'}",
        passed=passed,
        issues=issues,
    )
    
    new_state = {
        **state,
        "quality_passed":        passed,
        "quality_issues":        issues,
        "quality_reroute_count": reroute_count + (0 if passed else 1),
        "graph_plan":            list(state.get("graph_plan") or []) + [step],
    }

    if not passed:
        # Reset resolution so the subgraph runs again
        new_state["is_resolved"] = False
        if quality_gate_feedback:
            new_state["quality_gate_feedback"] = quality_gate_feedback
    else:
        # Clear feedback if passed
        new_state.pop("quality_gate_feedback", None)

    return new_state


def notify_user_node(state: TicketState) -> TicketState:
    """Final node — logs resolution. Ensures final_response uses the email template."""
    from app.core.tools.ticket_tools import notify_user
    tid = state.get("ticket_id", "unknown")
    first_name = state.get("user_first_name", "")

    # Early-exit responses (junk / invalid domain / unregistered) bypass promote_draft.
    # Apply the email template here if the response hasn't been wrapped yet.
    final = state.get("final_response", "")
    final = _format_email_response(final, first_name)

    logger.info(
        f"[notify_user] ticket={tid} user={state.get('email')} "
        f"response_len={len(final)} chars"
    )
    step = _plan_step(
        tid,
        "notify_user",
        "Resolution delivered to user." if not state.get("needs_clarification") else "Clarification question sent to user.",
        needs_clarification=state.get("needs_clarification", False),
    )
    
    state = {**state, "final_response": final, "graph_plan": list(state.get("graph_plan") or []) + [step]}
    return notify_user(state)


def human_queue_node(state: TicketState) -> TicketState:
    """Escalation node — logs and marks ticket as escalated."""
    from app.core.tools.ticket_tools import send_to_human_queue
    tid    = state.get("ticket_id", "unknown")
    reason = state.get("escalation_reason") or state.get("routing_reason", "No reason provided.")
    logger.warning(
        f"[human_queue] ticket={tid} user={state.get('email')} reason={reason}"
    )
    step = _plan_step(tid, "human_queue", f"Escalated to human agent. Reason: {reason}")
    
    state = {
        **state,
        "escalation_reason": reason,
        "graph_plan":        list(state.get("graph_plan") or []) + [step],
    }
    return send_to_human_queue(state)


# ── Quality gate routing ──────────────────────────────────────────────────────

def after_quality_gate(state: TicketState) -> Literal["notify_user", "router_node", "human_queue"]:
    if state.get("quality_passed"):
        return "notify_user"
    if state.get("quality_reroute_count", 0) >= MAX_QUALITY_REROUTES:
        return "human_queue"
    return "router_node"


# ── Build graph ───────────────────────────────────────────────────────────────

def _build_graph() -> "CompiledGraph":
    g = StateGraph(TicketState)

    g.add_node("intake_node",                       intake_node)
    g.add_node("router_node",                       router_node)
    # Fully implemented subgraphs
    g.add_node("certificate_subgraph",              run_certificate_subgraph)
    g.add_node("courses_subgraph",                  run_courses_subgraph)
    g.add_node("login_and_registration_subgraph",   run_login_and_registration_subgraph)
    g.add_node("profile_update_subgraph",           run_profile_update_subgraph)
    # Stub subgraphs
    g.add_node("ca_apar_subgraph",                  run_ca_apar_subgraph)
    g.add_node("organisation_subgraph",             run_organisation_subgraph)
    g.add_node("user_service_request_subgraph",     run_user_service_request_subgraph)
    g.add_node("general_query_subgraph",            run_general_query_subgraph)
    g.add_node("mobile_application_subgraph",       run_mobile_application_subgraph)
    g.add_node("virtual_event_subgraph",            run_virtual_event_subgraph)
    g.add_node("program_subgraph",                  run_program_subgraph)
    # Utility nodes
    g.add_node("promote_draft",                     promote_draft_to_final)
    g.add_node("quality_gate",                      quality_gate_node)
    g.add_node("notify_user",                       notify_user_node)
    g.add_node("human_queue",                       human_queue_node)

    g.set_entry_point("intake_node")

    g.add_conditional_edges(
        "intake_node",
        after_intake,
        {
            "router_node": "router_node",
            "notify_user": "notify_user",
        }
    )

    g.add_conditional_edges(
        "router_node",
        route_decision,
        {
            # Fully implemented
            "certificate_subgraph":              "certificate_subgraph",
            "courses_subgraph":                  "courses_subgraph",
            "login_and_registration_subgraph":   "login_and_registration_subgraph",
            "profile_update_subgraph":           "profile_update_subgraph",
            # Stubs
            "ca_apar_subgraph":                  "ca_apar_subgraph",
            "organisation_subgraph":             "organisation_subgraph",
            "user_service_request_subgraph":     "user_service_request_subgraph",
            "general_query_subgraph":            "general_query_subgraph",
            "mobile_application_subgraph":       "mobile_application_subgraph",
            "virtual_event_subgraph":            "virtual_event_subgraph",
            "program_subgraph":                  "program_subgraph",
            "human_queue":                       "human_queue",
        }
    )

    for sub in (
        # Fully implemented
        "certificate_subgraph", "courses_subgraph",
        "login_and_registration_subgraph", "profile_update_subgraph",
        # Stubs
        "ca_apar_subgraph", "organisation_subgraph", "user_service_request_subgraph",
        "general_query_subgraph", "mobile_application_subgraph",
        "virtual_event_subgraph", "program_subgraph",
    ):
        g.add_edge(sub, "promote_draft")

    g.add_edge("promote_draft", "quality_gate")

    g.add_conditional_edges(
        "quality_gate",
        after_quality_gate,
        {
            "notify_user": "notify_user",
            "router_node": "router_node",
            "human_queue": "human_queue",
        }
    )

    g.add_edge("notify_user", END)
    g.add_edge("human_queue", END)

    return g.compile()


graph = _build_graph()
logger.info("[main_graph] iGOT Karmayogi Resolution Graph compiled successfully.")


# ── Public API ────────────────────────────────────────────────────────────────

def run_ticket(ticket: dict) -> TicketState:
    """
    Synchronous entry point.
    Args: ticket dict with at minimum { email, message }.
    """
    logger.info(f"[main_graph] run_ticket id={ticket.get('ticket_id')}")
    return graph.invoke(ticket)


async def arun_ticket(ticket: dict) -> TicketState:
    """
    Async entry point.
    Args: ticket dict with at minimum { email, message }.
    """
    logger.info(f"[main_graph] arun_ticket id={ticket.get('ticket_id')}")
    return await graph.ainvoke(ticket)
