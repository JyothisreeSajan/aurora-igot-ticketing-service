"""
nodes/router_node.py
--------------------
Stage 2 of the iGOT Karmayogi Resolution Graph.

Reads the enriched TicketState (category + confidence) and decides which
specialist subgraph should handle the ticket:

  Fully implemented:
  - certificate_subgraph              : certificate not generated / download issues
  - courses_subgraph                  : enrollment, progress, content, external portals
  - login_and_registration_subgraph   : login, email/mobile update, multi-account
  - profile_update_subgraph           : verification, designation, leaderboard

  Stub (→ support ticket until SOP is built):
  - ca_apar_subgraph                  : APAR training plan / assessment issues
  - organisation_subgraph             : domain, MDO, ATI/CTI, deletion requests
  - user_service_request_subgraph     : account/email/designation/role update requests
  - mobile_application_subgraph       : app not loading
  - virtual_event_subgraph            : event join / search issues
  - program_subgraph                  : program-level assessment issues
  - general_query_subgraph            : unmatched / informational queries

  - human_queue                       : low-confidence tickets or quality-gate failures

The actual graph branching is done via the conditional edge in main_graph.py.
This node only writes `route_to` and `routing_reason` into state.
"""

import logging
from datetime import datetime

from app.core.graph.state import TicketState

logger = logging.getLogger(__name__)


from app.core.utils.ticket_tracker import ticket_tracker


def _plan_step(ticket_id: str, node: str, detail: str, **extra) -> dict:
    step_dict = {"node": node, "detail": detail, "timestamp": datetime.now().strftime("%H:%M:%S"), **extra}
    if ticket_id and ticket_id != "unknown":
        ticket_tracker.add_step(ticket_id, node, detail, extra)
    return step_dict


# ── Thresholds ────────────────────────────────────────────────────────────────
CONFIDENCE_THRESHOLD = 0.75
MAX_QUALITY_REROUTES = 2

# ── Keyword → subgraph routing ────────────────────────────────────────────────
# Checks are applied in order; first match wins.
# Each key mirrors the exact snake_case category name from CATEGORY_SUBCATEGORY_MAP.
CATEGORY_ROUTING_RULES = [
    # ── Fully implemented subgraphs ────────────────────────────────────────────
    (["certificate"],                                      "certificate_subgraph"),
    (["course"],                                           "courses_subgraph"),
    (["login_issue", "login"],                            "login_and_registration_subgraph"),
    (["profile_update"],                                   "profile_update_subgraph"),
    # ── Stub subgraphs (create support ticket until SOP is built) ──────────────
    (["ca_apar_issue", "ca_apar", "apar"],               "ca_apar_subgraph"),
    (["organisation_request", "organisation"],            "organisation_subgraph"),
    (["user_service_request"],                             "user_service_request_subgraph"),
    (["mobile_application"],                               "mobile_application_subgraph"),
    (["virtual_event"],                                    "virtual_event_subgraph"),
    (["program"],                                          "program_subgraph"),
    (["general"],                                          "general_query_subgraph"),
]
DEFAULT_SUBGRAPH = "general_query_subgraph"


def _resolve_subgraph(category: str, sop_categories: list) -> str:
    """
    Determine the target subgraph from the classified category.
    Uses keyword matching against CATEGORY_ROUTING_RULES; falls back to DEFAULT_SUBGRAPH.
    """
    cat_lower = category.lower()
    for keywords, subgraph_name in CATEGORY_ROUTING_RULES:
        if any(kw in cat_lower for kw in keywords):
            return subgraph_name
    return DEFAULT_SUBGRAPH


def router_node(state: TicketState) -> TicketState:
    """
    Pure routing decision based on category classification.
    Writes route_to and routing_reason into state.

    Fast-path: if is_continuation=True, route directly to the stored subgraph
    without any classification or confidence checking.
    """
    ticket_id    = state.get("ticket_id", "unknown")
    category     = state.get("category", "general")
    confidence   = state.get("confidence", 0.0)
    reroutes     = state.get("quality_reroute_count", 0)
    sop_cats     = state.get("sop_categories", [])
    email        = state.get("email", "unknown")

    # ── Continuation fast-path ─────────────────────────────────────────────────
    if state.get("is_continuation"):
        saved_route = state.get("route_to", DEFAULT_SUBGRAPH)
        reason = (
            f"Continuation of open ticket '{ticket_id}' (user: {email}). "
            f"Routing directly to '{saved_route}' without re-classification."
        )
        logger.info(f"[router] {reason}")
        step = _plan_step(
            ticket_id,
            "router_node",
            f"Continuation: routing directly to '{saved_route}' (skipping intake/classification).",
            decision=saved_route,
            is_continuation=True,
        )
        return {
            **state,
            "route_to":       saved_route,
            "routing_reason": reason,
            "graph_plan":     list(state.get("graph_plan") or []) + [step],
        }

    # ── Hard stop: too many quality-gate failures ──────────────────────────────
    if reroutes >= MAX_QUALITY_REROUTES:
        reason = (
            f"Quality gate failed {reroutes} times for ticket '{ticket_id}' "
            f"(user: {email}) — hard escalating to human queue to prevent loop."
        )
        logger.warning(f"[router] {reason}")
        step = _plan_step(ticket_id, "router_node", f"Hard escalation: quality gate failed {reroutes} times.", decision="human_queue")
        return {
            **state,
            "route_to":           "human_queue",
            "routing_reason":     reason,
            "escalated_to_human": True,
            "escalation_reason":  reason,
            "graph_plan":         list(state.get("graph_plan") or []) + [step],
        }

    # ── Confidence gate ───────────────────────────────────────────────────────
    if confidence < CONFIDENCE_THRESHOLD:
        reason = (
            f"Classification confidence {confidence:.2f} is below threshold "
            f"{CONFIDENCE_THRESHOLD} for SOP category '{category}'. "
            f"Routing to human queue for ticket '{ticket_id}' (user: {email})."
        )
        logger.info(f"[router] {reason}")
        step = _plan_step(ticket_id, "router_node", f"Low confidence ({confidence:.2f}) — routing to human queue.", decision="human_queue")
        return {
            **state,
            "route_to":           "human_queue",
            "routing_reason":     reason,
            "escalated_to_human": True,
            "escalation_reason":  reason,
            "graph_plan":         list(state.get("graph_plan") or []) + [step],
        }

    # ── Route to the right specialist subgraph ────────────────────────────────
    subgraph = _resolve_subgraph(category, sop_cats)
    reason = (
        f"Confidence {confidence:.2f} >= threshold. "
        f"SOP category '{category}' → '{subgraph}' for ticket '{ticket_id}' (user: {email})."
    )
    logger.info(f"[router] {reason}")

    step = _plan_step(ticket_id, "router_node", f"Routing category='{category}' → '{subgraph}'.", decision=subgraph, confidence=confidence)
    return {
        **state,
        "route_to":       subgraph,
        "routing_reason": reason,
        "graph_plan":     list(state.get("graph_plan") or []) + [step],
    }


def route_decision(state: TicketState) -> str:
    """
    Conditional edge function used by main_graph.py.
    Returns the name of the next node to execute.
    """
    return state.get("route_to", "human_queue")
