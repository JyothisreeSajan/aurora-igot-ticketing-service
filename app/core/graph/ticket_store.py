"""
ticket_store.py
---------------
Singleton TicketStore — persists iGOT resolution tickets to ElasticSearch
and handles continuation detection for multi-turn conversations.

The ES index is prefixed at runtime as:
  {AURORA_APPLICATION_NAME}_{AURORA_APPLICATION_ENVIRONMENT}_{ELASTICSEARCH_BOT_INTERACTION_INDEX}
  e.g. igot_aurora_prod_agent_interaction

ES document schema:
{
  "ticket_id":              str (uuid4),
  "email":                  str,
  "status":                 "open" | "resolved" | "escalated",
  "awaiting_clarification": bool,
  "category":               str,
  "main_category":          str,
  "route_to":               str,
  "messages":               [{"role": "user"|"agent", "content": str, "timestamp": ISO}],
  "clarification_question": str,
  "partial_match":          bool,
  "graph_plan":             list,
  "retry_count":            int,
  "final_response":         str,
  "created_at":             ISO,
  "updated_at":             ISO
}
"""

import logging
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.utils.constants import GraphStage, get_llm_model
from app.core.utils.es_utils import es_manager

logger = logging.getLogger(__name__)

ES_INDEX = "aurora_igot_user_tickets"
_llm     = ChatGoogleGenerativeAI(model=get_llm_model(GraphStage.TICKET_STORE), temperature=0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ticket_status(state: dict) -> str:
    if state.get("escalated_to_human"):
        return "escalated"
    if state.get("needs_clarification") or state.get("partial_match"):
        return "open"
    return "resolved"


def _build_messages(state: dict) -> list:
    """Compose message list from state (preserves existing messages, appends new ones)."""
    ts = _now()
    msgs = list(state.get("conversation_messages") or [])

    # Append the latest user message if not already there
    raw_msg = state.get("message", "")
    if raw_msg and (not msgs or msgs[-1].get("content") != raw_msg or msgs[-1].get("role") != "user"):
        msgs.append({"role": "user", "content": raw_msg, "timestamp": ts})

    # Append agent response if we have one
    agent_resp = state.get("final_response", "")
    if agent_resp and (not msgs or msgs[-1].get("role") != "agent"):
        msgs.append({"role": "agent", "content": agent_resp, "timestamp": ts})

    return msgs


class TicketStore:

    def __init__(self):
        """Pre-create the ES index at startup so it is ready before the first ticket arrives."""
        # es_manager may still be None at module import time; _ensure_index handles that gracefully.
        self._ensure_index()

    def _client(self):
        c = es_manager.client
        if not c:
            raise RuntimeError("ES client not available — ticket_store is offline.")
        return c

    # ── Ensure index exists ───────────────────────────────────────────────────

    def _ensure_index(self):
        """Create the index with minimal mappings if it doesn't exist yet."""
        c = es_manager.client
        if not c:
            logger.debug("[ticket_store] _ensure_index skipped — ES client not available yet.")
            return
        try:
            if not c.indices.exists(index=ES_INDEX):
                c.indices.create(
                    index=ES_INDEX,
                    body={
                        "mappings": {
                            "_doc": {
                                "properties": {
                                    "ticket_id":              {"type": "keyword"},
                                    "email":                  {"type": "keyword"},
                                    "status":                 {"type": "keyword"},
                                    "awaiting_clarification": {"type": "boolean"},
                                    "category":               {"type": "keyword"},
                                    "main_category":          {"type": "keyword"},
                                    "sub_category":           {"type": "keyword"},
                                    "sub_category_label":     {"type": "keyword"},
                                    "route_to":               {"type": "keyword"},
                                    "clarification_question": {"type": "text"},
                                    "partial_match":          {"type": "boolean"},
                                    "final_response":         {"type": "text"},
                                    "retry_count":            {"type": "integer"},
                                    "created_at":             {"type": "date"},
                                    "updated_at":             {"type": "date"},
                                }
                            }
                        }
                    },
                )
                logger.info(f"[ticket_store] Created ES index '{ES_INDEX}'.")
        except Exception as e:
            logger.warning(f"[ticket_store] _ensure_index: {e}")

    # ── Create ────────────────────────────────────────────────────────────────

    def create_ticket(self, state: dict) -> str:
        """Index a new ticket doc. Returns ticket_id."""
        ticket_id = state.get("ticket_id") or state.get("interaction_id")
        if not ticket_id:
            import uuid
            ticket_id = str(uuid.uuid4())

        self._ensure_index()
        ts = _now()
        is_awaiting = bool(state.get("needs_clarification") or state.get("partial_match"))

        doc = {
            "ticket_id":              ticket_id,
            "email":                  state.get("email", ""),
            "status":                 _ticket_status(state),
            "awaiting_clarification": is_awaiting,
            "category":               state.get("category", ""),
            "main_category":          state.get("main_category", ""),
            "sub_category":           state.get("sub_category", ""),
            "sub_category_label":     state.get("sub_category_label", ""),
            "route_to":               state.get("route_to", ""),
            "messages":               _build_messages(state),
            "clarification_question": state.get("final_response", "") if is_awaiting else "",
            "partial_match":          bool(state.get("partial_match", False)),
            "graph_plan":             state.get("graph_plan", []),
            "retry_count":            state.get("retry_count", 0),
            "final_response":         state.get("final_response", ""),
            "created_at":             ts,
            "updated_at":             ts,
        }

        try:
            self._client().index(index=ES_INDEX, id=ticket_id, document=doc)
            logger.info(f"[ticket_store] Created ticket={ticket_id} status={doc['status']} "
                        f"awaiting={is_awaiting} user={doc['email']}")
        except Exception as e:
            logger.error(f"[ticket_store] create_ticket failed: {e}")

        return ticket_id

    # ── Update ────────────────────────────────────────────────────────────────

    def update_ticket(self, ticket_id: str, state: dict):
        """Update an existing ticket with the latest graph result."""
        is_awaiting = bool(state.get("needs_clarification") or state.get("partial_match"))
        ts = _now()

        update_doc = {
            "status":                 _ticket_status(state),
            "awaiting_clarification": is_awaiting,
            "category":               state.get("category", ""),
            "main_category":          state.get("main_category", ""),
            "sub_category":           state.get("sub_category", ""),
            "sub_category_label":     state.get("sub_category_label", ""),
            "route_to":               state.get("route_to", ""),
            "messages":               _build_messages(state),
            "clarification_question": state.get("final_response", "") if is_awaiting else "",
            "partial_match":          bool(state.get("partial_match", False)),
            "graph_plan":             state.get("graph_plan", []),
            "retry_count":            state.get("retry_count", 0),
            "final_response":         state.get("final_response", ""),
            "updated_at":             ts,
        }

        try:
            self._client().update(index=ES_INDEX, doc_type="_doc", id=ticket_id, body={"doc": update_doc})
            logger.info(f"[ticket_store] Updated ticket={ticket_id} status={update_doc['status']} "
                        f"awaiting={is_awaiting}")
        except Exception as e:
            logger.error(f"[ticket_store] update_ticket failed: {e}")

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_open_clarification_ticket(self, email: str) -> dict | None:
        """
        Returns the most recent open ticket that is awaiting clarification for this email,
        or None if no such ticket exists.
        """
        try:
            resp = self._client().search(
                index=ES_INDEX,
                query={
                    "bool": {
                        "must": [
                            {"term": {"email":                  email}},
                            {"term": {"status":                 "open"}},
                            {"term": {"awaiting_clarification": True}},
                        ]
                    }
                },
                sort=[{"updated_at": {"order": "desc"}}],
                size=1,
            )
            hits = resp.get("hits", {}).get("hits", [])
            if hits:
                doc = hits[0]["_source"]
                logger.info(f"[ticket_store] Found open ticket={doc.get('ticket_id')}")
                return doc
        except Exception as e:
            logger.error(f"[ticket_store] get_open_clarification_ticket failed: {e}")
        return None

    def get_ticket(self, ticket_id: str) -> dict | None:
        """
        Returns a specific ticket by ID, or None if it doesn't exist.
        """
        try:
            resp = self._client().get(index=ES_INDEX, id=ticket_id)
            if resp and "_source" in resp:
                return resp["_source"]
        except Exception as e:
            # 404 is normal if it doesn't exist
            if getattr(e, "status_code", None) != 404 and "NotFoundError" not in str(type(e)):
                logger.error(f"[ticket_store] get_ticket failed for {ticket_id}: {e}")
        return None

    def get_tickets(
        self,
        ticket_id: str | None = None,
        limit: int = 10,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list:
        """
        Returns a list of tickets. If ticket_id is provided, returns that specific ticket.
        Otherwise, returns the latest `limit` tickets sorted by created_at desc.

        Optional filters:
          - date_from: ISO date string — only include tickets created on/after this date
          - date_to:   ISO date string — only include tickets created on/before this date
        """
        try:
            must_clauses = []

            if ticket_id:
                must_clauses.append({"term": {"ticket_id": ticket_id}})

            if date_from or date_to:
                date_range: dict = {}
                if date_from:
                    date_range["gte"] = date_from
                if date_to:
                    date_range["lte"] = date_to
                must_clauses.append({"range": {"created_at": date_range}})

            query = {"bool": {"must": must_clauses}} if must_clauses else {"match_all": {}}

            resp = self._client().search(
                index=ES_INDEX,
                query=query,
                sort=[{"created_at": {"order": "desc"}}],
                size=1 if ticket_id else limit,
            )

            hits = resp.get("hits", {}).get("hits", [])
            return [hit["_source"] for hit in hits]
        except Exception as e:
            logger.error(f"[ticket_store] get_tickets failed: {e}")
            return []

    # ── Delete ────────────────────────────────────────────────────────────────

    def delete_old_resolved_tickets(self, days_old: int = 10) -> dict:
        """
        Deletes tickets that meet ALL of the following criteria:
        1. status = "resolved"
        2. awaiting_clarification = False
        3. updated_at is older than `days_old` days from now

        Returns: {"deleted_count": int, "errors": list}
        """
        from datetime import datetime, timedelta, timezone

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_old)
        cutoff_iso = cutoff_date.isoformat()

        try:
            # Find matching tickets
            resp = self._client().search(
                index=ES_INDEX,
                query={
                    "bool": {
                        "must": [
                            {"term": {"status": "resolved"}},
                            {"term": {"awaiting_clarification": False}},
                            {"range": {"updated_at": {"lt": cutoff_iso}}},
                        ]
                    }
                },
                size=10000,  # ES default max window
                _source=["ticket_id"],
            )

            hits = resp.get("hits", {}).get("hits", [])
            ticket_ids = [hit["_id"] for hit in hits]

            if not ticket_ids:
                logger.info(f"[ticket_store] No tickets found matching deletion criteria (>{days_old} days old, resolved, not awaiting clarification)")
                return {"deleted_count": 0, "errors": []}

            # Delete by query
            delete_resp = self._client().delete_by_query(
                index=ES_INDEX,
                query={
                    "bool": {
                        "must": [
                            {"term": {"status": "resolved"}},
                            {"term": {"awaiting_clarification": False}},
                            {"range": {"updated_at": {"lt": cutoff_iso}}},
                        ]
                    }
                },
            )

            deleted_count = delete_resp.get("deleted", 0)
            failures = delete_resp.get("failures", [])

            logger.info(
                f"[ticket_store] Deleted {deleted_count} resolved tickets older than {days_old} days "
                f"(cutoff: {cutoff_iso})"
            )

            return {
                "deleted_count": deleted_count,
                "errors": failures,
            }

        except Exception as e:
            logger.error(f"[ticket_store] delete_old_resolved_tickets failed: {e}")
            return {
                "deleted_count": 0,
                "errors": [str(e)],
            }

    # ── Continuation detection ────────────────────────────────────────────────

    def is_continuation_reply(
        self,
        open_ticket: dict,
        new_message: str,
        ticket_id: str = "",
        email: str = "",
    ) -> bool:
        """
        LLM-based disambiguation: is the new_message a direct reply to the
        clarification question in open_ticket, or is it a brand-new unrelated request?

        Returns True  → continuation (resume old ticket)
                False → new request
        """
        from app.core.utils.helpers import mask_pii_default

        clarification_q  = mask_pii_default(open_ticket.get("clarification_question", ""))
        original_message = ""
        for m in (open_ticket.get("messages") or []):
            if m.get("role") == "user":
                original_message = mask_pii_default(m.get("content", ""))
                break

        prompt = (
            "You are a routing assistant for iGOT Karmayogi support.\n\n"
            "An open support ticket exists with the following context:\n"
            f"  Original issue : {original_message}\n"
            f"  Agent asked    : {clarification_q}\n\n"
            f"The user has now sent: \"{mask_pii_default(new_message)}\"\n\n"
            "Is the user's new message a DIRECT REPLY to the agent's clarification question, "
            "or is it a completely NEW and unrelated support request?\n\n"
            "Rules:\n"
            "- If the message answers or addresses the clarification question → reply=true\n"
            "- If the message is about a clearly different topic → reply=false\n"
            "- If ambiguous, prefer reply=true (safer to try continuation first)\n\n"
            'Respond ONLY with JSON: {"reply": true/false, "reason": "<one sentence>"}'
        )

        try:
            resp = _llm.invoke([
                SystemMessage(content="You are a JSON-only classifier. No markdown."),
                HumanMessage(content=prompt),
            ])
            raw = resp.content.strip().lstrip("```json").lstrip("```").rstrip("```").strip()

            # Track token usage
            if ticket_id:
                from app.core.utils.token_tracker import token_tracker
                _usage = resp.usage_metadata or {}
                token_tracker.record(
                    ticket_id=ticket_id,
                    email=email,
                    model=_llm.model,
                    prompt_tokens=_usage.get("input_tokens", 0),
                    completion_tokens=_usage.get("output_tokens", 0),
                    total_tokens=_usage.get("total_tokens", 0),
                    node="continuation_check",
                    category="intake",
                )

            import json
            parsed = json.loads(raw)
            is_reply = bool(parsed.get("reply", False))
            reason   = parsed.get("reason", "")
            logger.info(f"[ticket_store] continuation_check: reply={is_reply} reason={reason}")
            return is_reply
        except Exception as e:
            logger.warning(f"[ticket_store] is_continuation_reply LLM failed: {e} — defaulting to False")
            return False


# Singleton
ticket_store = TicketStore()
