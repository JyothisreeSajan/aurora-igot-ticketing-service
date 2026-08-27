"""
nodes/intake_node.py
--------------------
Stage 1 of the iGOT Karmayogi Resolution Graph.

Input  : { email, message }   ← the only two user-provided fields
Output : enriched TicketState with category, main_category, sop_categories,
         confidence, enriched_context, and user_first_name.

Performs (in order):
  0. Email domain validation       → early exit if domain not whitelisted
  1. Junk detection                → early exit if message is spam / irrelevant
  2. User registration check       → early exit if email not on iGOT platform
  3. LLM two-level classification  (category + sub_category)
  4. Assemble enriched context
"""

import json
import logging
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.graph.state import TicketState
from app.core.tools.zoho_tools import update_zoho_ticket_direct
from app.core.utils.constants import (
    DOMAIN_INVALID_HTML_BODY,
    JUNK_CONFIDENCE_THRESHOLD,
    JUNK_HTML_BODY,
    UNREGISTERED_HTML_BODY,
    VALIDATE_EMAIL,
    GraphStage,
    build_email_html,
    get_llm_model,
)
from app.core.utils.helpers import (
    detect_junk,
    fetch_sop_categories,
    fetch_user_info,
    is_domain_allowed,
)
from app.core.utils.prompt_templates import (
    CATEGORY_SUBCATEGORY_MAP,
    CLASSIFICATION_PROMPT,
    INTAKE_CLASSIFIER_SYSTEM,
    _build_subcategory_hint,
)
from app.core.utils.token_tracker import token_tracker

logger = logging.getLogger(__name__)

_llm = ChatGoogleGenerativeAI(model=get_llm_model(GraphStage.TICKET_ROUTING), temperature=0)


from app.core.utils.ticket_tracker import ticket_tracker


def _plan_step(ticket_id: str, node: str, detail: str, **extra) -> dict:
    """Create a single graph_plan step dict and push it to Elasticsearch."""
    step_dict = {"node": node, "detail": detail, "timestamp": datetime.now().strftime("%H:%M:%S"), **extra}
    if ticket_id and ticket_id != "unknown":
        ticket_tracker.add_step(ticket_id, node, detail, extra)
    return step_dict


def intake_node(state: TicketState) -> TicketState:
    """Enrichment + junk detection + SOP classification node.

    For continuation turns (is_continuation=True), bypasses all classification
    and passes state through unchanged — routing info is already restored from ES.
    """
    ticket_id    = state.get("ticket_id", "unknown")
    email        = state.get("email", "")
    message      = state.get("message", "")
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    logger.info(f"[intake] ticket={ticket_id}")

    # ── Continuation bypass ───────────────────────────────────────────────────
    # State already has category / main_category / route_to restored from ES.
    # Skip all classification to avoid overwriting stored routing info.
    if state.get("is_continuation"):
        step = _plan_step(
            ticket_id,
            node="intake_node",
            detail=f"Continuation bypass — preserving stored routing "
                   f"(category='{state.get('category')}', route_to='{state.get('route_to')}').",
            is_continuation=True,
        )
        logger.info(f"[intake] Continuation bypass for ticket={ticket_id}")
        return {
            **state,
            "is_resolved":           False,
            "escalated_to_human":    False,
            "quality_reroute_count": state.get("quality_reroute_count", 0),
            "graph_plan":            list(state.get("graph_plan") or []) + [step],
        }

    # ── 0. Email domain validation (early exit) ───────────────────────────────
    if VALIDATE_EMAIL:
        logger.info(f"[intake] Validating email domain for ticket={ticket_id}")
        if not is_domain_allowed(email):
            domain = email.split("@")[-1] if "@" in email else email
            logger.info(f"[intake] Invalid email domain '{domain}' for ticket={ticket_id}")
            step = _plan_step(
                ticket_id,
                "intake_node",
                f"Invalid email domain '{domain}' — not in the iGOT platform whitelist.",
                is_invalid_domain=True,
            )
            update_zoho_ticket_direct(ticket_id, f"Invalid email domain '{domain}' — not in whitelist.", "Closed")
            return {
                **state,
                "is_junk":            False,
                "category":           "general",
                "main_category":      "general",
                "sub_category":       "",
                "sub_category_label": "",
                "confidence":         1.0,
                "final_response":     build_email_html(
                    DOMAIN_INVALID_HTML_BODY.format(email=email, domain=domain),
                    name="User",
                ),
                "is_resolved":        True,
                "graph_plan":         list(state.get("graph_plan") or []) + [step],
            }

    # ── 1. Junk detection (early exit) ────────────────────────────────────────
    is_junk, junk_conf, junk_reason = detect_junk(message, ticket_id=ticket_id, email=email)

    if is_junk and junk_conf >= JUNK_CONFIDENCE_THRESHOLD:
        logger.info(f"[intake] Junk detected (conf={junk_conf:.2f}): {junk_reason} for ticket={ticket_id}")
        step = _plan_step(
            ticket_id,
            "intake_node",
            f"Junk detected (conf={junk_conf:.2f}): {junk_reason}",
            is_junk=True,
            junk_confidence=junk_conf,
        )
        update_zoho_ticket_direct(ticket_id, f"Junk detected: {junk_reason}", "Closed")
        return {
            **state,
            "is_junk":            True,
            "junk_reason":        junk_reason,
            "category":           "junk",
            "main_category":      "junk",
            "sub_category":       "",
            "sub_category_label": "",
            "confidence":         junk_conf,
            "final_response":     build_email_html(JUNK_HTML_BODY, name="User"),
            "is_resolved":        True,
            "graph_plan":         list(state.get("graph_plan") or []) + [step],
        }

    # ── 1.5. User registration check (early exit) ─────────────────────────────
    logger.info(f"[intake] Checking user registration for ticket={ticket_id}")
    is_registered, user_first_name = fetch_user_info(email)
    if not is_registered:
        logger.info(f"[intake] Unregistered user for ticket={ticket_id}")
        step = _plan_step(
            ticket_id,
            "intake_node",
            "Unregistered user — not found on iGOT platform.",
            is_unregistered=True,
        )
        update_zoho_ticket_direct(ticket_id, "User not registered on iGOT platform.", "Closed")
        return {
            **state,
            "is_junk":            False,
            "category":           "general",
            "main_category":      "general",
            "sub_category":       "",
            "sub_category_label": "",
            "user_first_name":    user_first_name,
            "confidence":         1.0,
            "final_response":     build_email_html(
                UNREGISTERED_HTML_BODY.format(email=email),
                name="User",
            ),
            "is_resolved":        True,
            "graph_plan":         list(state.get("graph_plan") or []) + [step],
        }

    # ── 2. Discover live SOP categories ───────────────────────────────────────
    sop_categories = fetch_sop_categories()
    logger.debug(f"[intake] SOP categories: {sop_categories}")

    # ── 3. LLM two-level classification ──────────────────────────────────────
    category           = "general"
    main_category      = "general"
    sub_category       = ""
    sub_category_label = ""
    confidence         = 0.5
    reason             = ""

    # Reverse-lookup: human-readable label → snake_case key
    _label_to_key: dict[str, str] = {
        label: key
        for subs in CATEGORY_SUBCATEGORY_MAP.values()
        for key, label in subs
    }

    try:
        from app.core.utils.helpers import mask_pii_default
        prompt = CLASSIFICATION_PROMPT.format(
            categories=", ".join(sop_categories),
            subcategory_hint=_build_subcategory_hint(),
            message=mask_pii_default(message),
        )
        response = _llm.invoke([
            SystemMessage(content=INTAKE_CLASSIFIER_SYSTEM),
            HumanMessage(content=prompt),
        ])
        # Track classification LLM call
        _cls_usage = response.usage_metadata or {}
        token_tracker.record(
            ticket_id=ticket_id,
            email=email,
            model=_llm.model,
            prompt_tokens=_cls_usage.get("input_tokens", 0),
            completion_tokens=_cls_usage.get("output_tokens", 0),
            total_tokens=_cls_usage.get("total_tokens", 0),
            node="classification",
            category="intake",
        )
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            raw = raw.removeprefix("json")
        raw = raw.strip()

        parsed        = json.loads(raw)
        category      = parsed.get("category", "general").lower()
        main_category = parsed.get("main_category", category)
        confidence    = float(parsed.get("confidence", 0.5))
        reason        = parsed.get("reason", "")

        # Sub-category resolution
        raw_sub_label = parsed.get("sub_category", "").strip()
        if raw_sub_label and raw_sub_label in _label_to_key:
            sub_category_label = raw_sub_label
            sub_category       = _label_to_key[raw_sub_label]
        else:
            # Fallback: LLM may have returned the key instead of the label
            cat_subs = CATEGORY_SUBCATEGORY_MAP.get(category, [])
            matched  = next((k for k, _l in cat_subs if k == raw_sub_label), None)
            if matched:
                sub_category       = matched
                sub_category_label = next(l for k, l in cat_subs if k == matched)
            else:
                sub_category       = ""
                sub_category_label = ""

        if category not in [c.lower() for c in sop_categories]:
            if category == "general":
                logger.info(f"[intake] No specific SOP match, routing to general for ticket={ticket_id}")
                main_category = "general"
            else:
                logger.warning(f"[intake] Unknown category '{category}', falling back to 'general'")
                category      = "general"
                main_category = "general"
                confidence    = min(confidence, 0.6)

        logger.info(
            f"[intake] → category='{category}' main_category='{main_category}' "
            f"sub_category='{sub_category}' sub_category_label='{sub_category_label}' "
            f"conf={confidence:.2f}"
        )
    except Exception as e:
        logger.error(f"[intake] Classification failed: {e}")

    # ── 4. Assemble enriched context ──────────────────────────────────────────
    enriched_context = {
        "classification_reason": reason,
        "sop_categories":        sop_categories,
        "current_time":          current_time,
        "email":                 email,
    }

    _sub_display = sub_category_label or sub_category or "none"
    plan_step = _plan_step(
        ticket_id,
        node="intake_node",
        detail=(
            f"Classified as '{category}' / SOP='{main_category}' "
            f"/ sub='{_sub_display}' "
            f"(conf={confidence:.2f}). Reason: {reason}"
        ),
        sop_categories_available=sop_categories,
        sub_category=sub_category,
        sub_category_label=sub_category_label,
    )

    return {
        **state,
        "email":                 email,
        "category":              category,
        "main_category":         main_category,
        "sub_category":          sub_category,
        "sub_category_label":    sub_category_label,
        "user_first_name":       user_first_name,
        "sop_categories":        sop_categories,
        "confidence":            confidence,
        "enriched_context":      enriched_context,
        "retry_count":           state.get("retry_count", 0),
        "max_retries":           state.get("max_retries", 3),
        "is_resolved":           False,
        "escalated_to_human":    False,
        "quality_reroute_count": state.get("quality_reroute_count", 0),
        "graph_plan":            list(state.get("graph_plan") or []) + [plan_step],
    }
