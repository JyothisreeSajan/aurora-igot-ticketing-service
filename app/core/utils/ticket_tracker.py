"""
ticket_tracker.py
-----------------
Tracks the full lifecycle of a resolution ticket in ElasticSearch
under the index `ticket_status_index`.

Each ticket document is keyed by ticket_id and holds:
  - Core metadata (email, zoho ticket_id, category, timestamps)
  - A `stages` list that grows as the ticket passes through each
    pipeline stage: ingest → queued → intake → subgraph → quality_gate
    → notify_user / human_queue → completed
  - The final resolved state once the graph finishes

Public helpers
--------------
  tracker.create(ticket_id, email)              → called at ingest
  tracker.update_stage(ticket_id, stage, data)  → called at each pipeline stage
  tracker.complete(ticket_id, result)           → called after graph finishes
  tracker.get(ticket_id)                        → fetch single ticket by id
  tracker.list_all(size, from_)                 → paginated list of all tickets
"""

import logging
from datetime import datetime, timezone

from elasticsearch import Elasticsearch, NotFoundError

from app.core.utils.config import (
    AURORA_APPLICATION_ENVIRONMENT,
    AURORA_APPLICATION_NAME,
    ELASTICSEARCH_HOST,
    ELASTICSEARCH_PASSWORD,
    ELASTICSEARCH_USERNAME,
)

logger = logging.getLogger(__name__)

INDEX_NAME = "ticket_status_index"

# ── Stage constants ──────────────────────────────────────────────────────────
STAGE_QUEUED       = "queued"
STAGE_IN_PROGRESS  = "in-progress"
STAGE_RESOLVED     = "resolved"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TicketTracker:
    """Singleton class that writes ticket lifecycle events to ES."""

    def __init__(self):
        self.client: Elasticsearch | None = None
        self._setup()

    def _setup(self):
        if not ELASTICSEARCH_HOST:
            logger.warning("[ticket_tracker] ELASTICSEARCH_HOST not set — tracking disabled.")
            return
        try:
            self.client = Elasticsearch(
                ELASTICSEARCH_HOST,
                basic_auth=(
                    (ELASTICSEARCH_USERNAME, ELASTICSEARCH_PASSWORD)
                    if ELASTICSEARCH_USERNAME else None
                ),
                verify_certs=True,
            )
            if self.client.ping():
                self._ensure_index()
                logger.info(f"[ticket_tracker] Connected. Using index='{INDEX_NAME}'")
            else:
                logger.error("[ticket_tracker] ES ping failed.")
                self.client = None
        except Exception as e:
            logger.error(f"[ticket_tracker] ES setup error: {e}")
            self.client = None

    def _ensure_index(self):
        """Create the index with mappings if it doesn't exist."""
        if self.client.indices.exists(index=INDEX_NAME):
            return
        mappings = {
            "mappings": {
                "properties": {
                    "ticket_id":        {"type": "keyword"},
                    "email":            {"type": "keyword"},
                    "zoho_ticket_id":   {"type": "keyword"},
                    "current_stage":    {"type": "keyword"},
                    "is_continuation":  {"type": "boolean"},
                    "category":         {"type": "keyword"},
                    "main_category":    {"type": "keyword"},
                    "sub_category":     {"type": "keyword"},
                    "sub_category_label":{"type": "keyword"},
                    "subject":          {"type": "text"},
                    "body":             {"type": "text"},
                    "is_resolved":      {"type": "boolean"},
                    "escalated":        {"type": "boolean"},
                    "final_response":   {"type": "text"},
                    "created_at":       {"type": "date"},
                    "updated_at":       {"type": "date"},
                    "completed_at":     {"type": "date"},
                    "app_name":         {"type": "keyword"},
                    "environment":      {"type": "keyword"},
                    "stages": {
                        "type": "nested",
                        "properties": {
                            "stage":      {"type": "keyword"},
                            "timestamp":  {"type": "date"},
                            "detail":     {"type": "text"},
                            "extra":      {"type": "object", "dynamic": True},
                        }
                    }
                }
            }
        }
        try:
            self.client.indices.create(index=INDEX_NAME, body=mappings)
            logger.info(f"[ticket_tracker] Created index '{INDEX_NAME}'")
        except Exception as e:
            logger.warning(f"[ticket_tracker] Could not create index: {e}")

    # ── Write helpers ────────────────────────────────────────────────────────

    def create(self, ticket_id: str, email: str, zoho_ticket_id: str | None = None,
               is_continuation: bool = False, subject: str = "", body: str = "") -> bool:
        """
        Create a new tracking document at ingest time.
        Uses ticket_id as the ES document _id for easy lookup.
        """
        if not self.client:
            return False
        doc = {
            "ticket_id":       ticket_id,
            "email":           email,
            "zoho_ticket_id":  zoho_ticket_id or ticket_id,
            "current_stage":   STAGE_QUEUED,
            "is_continuation": is_continuation,
            "category":        None,
            "main_category":   None,
            "subject":         subject,
            "body":            body,
            "is_resolved":     False,
            "escalated":       False,
            "final_response":  None,
            "created_at":      _now(),
            "updated_at":      _now(),
            "completed_at":    None,
            "app_name":        AURORA_APPLICATION_NAME,
            "environment":     AURORA_APPLICATION_ENVIRONMENT,
            "stages": [
                {
                    "stage":     STAGE_QUEUED,
                    "timestamp": _now(),
                    "detail":    "Ticket ingested and queued for processing.",
                    "extra":     {"zoho_ticket_id": zoho_ticket_id},
                }
            ],
        }
        import time
        for attempt in range(3):
            try:
                self.client.index(index=INDEX_NAME, id=ticket_id, document=doc)
                logger.info(f"[ticket_tracker] Created tracking doc for ticket_id={ticket_id}")
                return True
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"[ticket_tracker] create failed for {ticket_id} (attempt {attempt+1}): {e}. Retrying...")
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"[ticket_tracker] create failed for {ticket_id} after 3 attempts: {e}")
                    return False

    def update_stage(self, ticket_id: str, stage: str, detail: str = "",
                     extra: dict | None = None) -> bool:
        """
        Append a new stage entry and update `current_stage` + `updated_at`.
        Safe to call even if the document doesn't exist yet (logs a warning).
        """
        if not self.client:
            return False
        stage_entry = {
            "stage":     stage,
            "timestamp": _now(),
            "detail":    detail,
            "extra":     extra or {},
        }
        script = {
            "source": """
                ctx._source.current_stage = params.stage;
                ctx._source.updated_at    = params.updated_at;
                ctx._source.stages.add(params.entry);
            """,
            "lang": "painless",
            "params": {
                "stage":      stage,
                "updated_at": _now(),
                "entry":      stage_entry,
            },
        }
        import time
        for attempt in range(3):
            try:
                self.client.update(index=INDEX_NAME, id=ticket_id, body={"script": script})
                logger.info(f"[ticket_tracker] Stage updated: ticket_id={ticket_id} stage={stage}")
                return True
            except NotFoundError:
                logger.warning(f"[ticket_tracker] update_stage called but doc not found: {ticket_id}")
                return False
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"[ticket_tracker] update_stage failed for {ticket_id} (attempt {attempt+1}): {e}. Retrying...")
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"[ticket_tracker] update_stage failed for {ticket_id} after 3 attempts: {e}")
                    return False

    def add_step(self, ticket_id: str, stage: str, detail: str = "", extra: dict | None = None) -> bool:
        """
        Append a detailed execution step to the `stages` list without changing `current_stage`.
        Useful for logging fine-grained graph node events in real-time.
        """
        if not self.client:
            return False
        step_entry = {
            "stage":     stage,
            "timestamp": _now(),
            "detail":    detail,
            "extra":     extra or {},
        }
        script = {
            "source": """
                ctx._source.updated_at = params.updated_at;
                ctx._source.stages.add(params.entry);
            """,
            "lang": "painless",
            "params": {
                "updated_at": _now(),
                "entry":      step_entry,
            },
        }
        import time
        for attempt in range(3):
            try:
                self.client.update(index=INDEX_NAME, id=ticket_id, body={"script": script})
                logger.debug(f"[ticket_tracker] Step logged: ticket_id={ticket_id} stage={stage}")
                return True
            except NotFoundError:
                logger.warning(f"[ticket_tracker] add_step called but doc not found: {ticket_id}")
                return False
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"[ticket_tracker] add_step failed for {ticket_id} (attempt {attempt+1}): {e}. Retrying...")
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"[ticket_tracker] add_step failed for {ticket_id} after 3 attempts: {e}")
                    return False

    def complete(self, ticket_id: str, result: dict) -> bool:
        """
        Mark ticket as completed after graph finishes.
        Stores category, final_response, escalation status, and full graph_plan.
        """
        if not self.client:
            return False

        is_escalated = result.get("escalated_to_human", False)

        stage_entry = {
            "stage":     STAGE_RESOLVED,
            "timestamp": _now(),
            "detail":    "Graph execution completed." if not is_escalated else "Escalated to human agent.",
            "extra": {
                "quality_passed": result.get("quality_passed"),
                "retry_count":    result.get("retry_count", 0),
                "graph_plan":     result.get("graph_plan", []),
            },
        }

        script = {
            "source": """
                ctx._source.current_stage  = params.stage;
                ctx._source.is_resolved    = params.is_resolved;
                ctx._source.escalated      = params.escalated;
                ctx._source.category       = params.category;
                ctx._source.main_category  = params.main_category;
                ctx._source.sub_category   = params.sub_category;
                ctx._source.sub_category_label = params.sub_category_label;
                ctx._source.final_response = params.final_response;
                ctx._source.updated_at     = params.now;
                ctx._source.completed_at   = params.now;
                ctx._source.stages.add(params.entry);
            """,
            "lang": "painless",
            "params": {
                "stage":          STAGE_RESOLVED,
                "is_resolved":    result.get("is_resolved", False),
                "escalated":      is_escalated,
                "category":       result.get("category"),
                "main_category":  result.get("main_category"),
                "sub_category":   result.get("sub_category"),
                "sub_category_label": result.get("sub_category_label"),
                "final_response": result.get("final_response", ""),
                "now":            _now(),
                "entry":          stage_entry,
            },
        }
        import time
        for attempt in range(3):
            try:
                self.client.update(index=INDEX_NAME, id=ticket_id, body={"script": script})
                logger.info(f"[ticket_tracker] Completed ticket_id={ticket_id} stage={STAGE_RESOLVED}")
                return True
            except NotFoundError:
                logger.warning(f"[ticket_tracker] complete called but doc not found: {ticket_id}")
                return False
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"[ticket_tracker] complete failed for {ticket_id} (attempt {attempt+1}): {e}. Retrying...")
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"[ticket_tracker] complete failed for {ticket_id} after 3 attempts: {e}")
                    return False

    # ── Read helpers ─────────────────────────────────────────────────────────

    def get(self, ticket_id: str) -> dict | None:
        """Fetch full tracking document for a specific ticket_id."""
        if not self.client:
            return None
        try:
            resp = self.client.get(index=INDEX_NAME, id=ticket_id)
            doc = resp["_source"]
            doc["_id"] = resp["_id"]
            return doc
        except NotFoundError:
            return None
        except Exception as e:
            logger.error(f"[ticket_tracker] get failed for {ticket_id}: {e}")
            return None

    def list_all(self, size: int = 50, from_: int = 0,
                 email: str | None = None,
                 stage: str | None = None,
                 date_from: str | None = None,
                 date_to: str | None = None) -> dict:
        """
        Paginated list of all tickets, newest first.

        Optional filters:
          - email:     filter by sender email
          - stage:     filter by current_stage
          - date_from: ISO date string — only include tickets created on or after this date
          - date_to:   ISO date string — only include tickets created on or before this date
        """
        if not self.client:
            return {"total": 0, "tickets": []}

        must_clauses = []
        if email:
            must_clauses.append({"term": {"email": email}})
        if stage:
            must_clauses.append({"term": {"current_stage": stage}})
        if date_from or date_to:
            date_range: dict = {}
            if date_from:
                date_range["gte"] = date_from
            if date_to:
                date_range["lte"] = date_to
            must_clauses.append({"range": {"created_at": date_range}})

        query = {"bool": {"must": must_clauses}} if must_clauses else {"match_all": {}}

        try:
            resp = self.client.search(
                index=INDEX_NAME,
                body={
                    "query": query,
                    "sort":  [{"created_at": {"order": "desc"}}],
                    "from": from_,
                    "size": size,
                    "_source": ["ticket_id", "email", "zoho_ticket_id", "current_stage", "subject", "body", "created_at", "updated_at", "stages"]
                }
            )
            hits = resp["hits"]["hits"]
            tickets = []
            for h in hits:
                doc = h["_source"]
                doc["_id"] = h["_id"]
                tickets.append(doc)
            total = resp["hits"]["total"]["value"]
            return {"total": total, "tickets": tickets}
        except Exception as e:
            logger.error(f"[ticket_tracker] list_all failed: {e}")
            return {"total": 0, "tickets": []}


# Singleton
ticket_tracker = TicketTracker()
