"""
app/api/feedback/router.py
--------------------------
POST endpoint to collect resolution feedback from L1 support agents or users.

Records are stored in the `aurora_resolution_feedback` Elasticsearch index
which is auto-created (with explicit mapping) when the application starts via
ESManager._ensure_indices().

The ticket must exist in `aurora_igot_user_tickets` and the submitted draft_id
must be one of the Aurora drafts stored on it (404 otherwise).

One record is kept per (ticket_id, draft_id): if feedback already exists for the
pair it is updated in place, otherwise a new document is created with
"<ticket_id>_<draft_id>" as its _id (so concurrent first submissions cannot
create duplicates).

Endpoint
--------
POST /api/v1/feedback/resolution
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from elasticsearch import NotFoundError
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.core.utils.es_utils import es_manager

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request / Response schemas ────────────────────────────────────────────────

class ResolutionFeedbackRequest(BaseModel):
    """Payload for submitting resolution feedback on a ticket."""

    ticket_id: str = Field(..., description="Unique identifier of the resolved ticket")
    is_helpful: bool = Field(..., description="Whether the resolution was helpful")
    category: Optional[str] = Field("", description="Ticket category")
    comments: Optional[str] = Field("", description="Free-text feedback comments")
    submitted_on: Optional[datetime] = Field(
        None,
        description="Timestamp when feedback was submitted (defaults to now if omitted)",
    )
    ticket_created_on: Optional[datetime] = Field(
        None, description="Timestamp when the original ticket was created"
    )
    l1_support_team: Optional[str] = Field("", description="Name or ID of the L1 support team handling the ticket")
    draft_id: str = Field(
        ..., min_length=1,
        description="Zoho draft id the feedback is about; must be a draft Aurora created on this ticket",
    )

    @field_validator("draft_id")
    @classmethod
    def _strip_draft_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("draft_id must not be blank")
        return value


class ResolutionFeedbackResponse(BaseModel):
    """Response returned after successfully storing feedback."""

    success: bool
    message: str
    action: str = Field(..., description="created | updated")
    document_id: Optional[str] = None
    index: str


# Fields an update only rewrites when the caller actually sent them.
_OPTIONAL_FIELDS = ("category", "comments", "l1_support_team", "ticket_created_on")


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _build_feedback_doc(payload: ResolutionFeedbackRequest, now: datetime) -> dict:
    return {
        "ticket_id":         payload.ticket_id,
        "draft_id":          payload.draft_id,
        "is_helpful":        payload.is_helpful,
        "category":          payload.category or "",
        "comments":          payload.comments or "",
        "submitted_on":      (payload.submitted_on or now).isoformat(),
        "ticket_created_on": payload.ticket_created_on.isoformat() if payload.ticket_created_on else None,
        "l1_support_team":   payload.l1_support_team or "",
    }


def _ticket_has_draft(ticket_id: str, draft_id: str) -> Tuple[bool, bool]:
    """Return (ticket_exists, draft_on_ticket), fetching only the id/draft fields."""
    try:
        res = es_manager.client.get(
            index=es_manager.user_tickets_index,
            id=ticket_id,
            _source=["ticket_id", "zoho_draft_id", "zoho_draft_ids"],
        )
    except NotFoundError:
        return False, False
    src = res.get("_source", {})
    ids = {str(i) for i in (src.get("zoho_draft_ids") or [])}
    if src.get("zoho_draft_id"):
        ids.add(str(src["zoho_draft_id"]))
    return True, draft_id in ids


def _find_existing_feedback(index_name: str, ticket_id: str, draft_id: str) -> Optional[str]:
    """Return the _id of the feedback for this ticket + draft, or None."""
    res = es_manager.client.search(
        index=index_name,
        body={
            "query": {"bool": {"filter": [
                {"term": {"ticket_id": ticket_id}},
                {"term": {"draft_id": draft_id}},
            ]}},
            "_source": ["ticket_id", "draft_id"],
            "sort": [{"submitted_on": {"order": "desc", "missing": "_last"}}],
            "size": 1,
        },
    )
    hits = res.get("hits", {}).get("hits", [])
    return hits[0]["_id"] if hits else None


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post(
    "/resolution",
    response_model=ResolutionFeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit resolution feedback",
    description=(
        "Stores L1 support / user feedback for an Aurora draft on a ticket into the "
        "`aurora_resolution_feedback` Elasticsearch index. The ticket and draft_id must "
        "exist in `aurora_igot_user_tickets` (404 otherwise). If feedback already exists "
        "for the ticket_id + draft_id it is updated instead of creating a new record."
    ),
)
async def submit_resolution_feedback(payload: ResolutionFeedbackRequest):
    """
    Accept resolution feedback and persist it to ElasticSearch, updating the
    existing feedback record for the ticket + draft when there is one.

    Raises
    ------
    404  Not Found – ticket is unknown, or draft_id is not an Aurora draft on it.
    503  Service Unavailable – when the ES client is not connected.
    500  Internal Server Error – on unexpected indexing failures.
    """
    if not es_manager.client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ElasticSearch is not available. Feedback cannot be stored.",
        )

    now = datetime.now(timezone.utc)

    try:
        ticket_exists, draft_found = _ticket_has_draft(payload.ticket_id, payload.draft_id)
        if not ticket_exists:
            raise _not_found(f"Ticket '{payload.ticket_id}' not found.")
        if not draft_found:
            raise _not_found(f"Draft '{payload.draft_id}' not found on ticket '{payload.ticket_id}'.")

        index_name = es_manager.resolution_feedback_index
        doc_id = _find_existing_feedback(index_name, payload.ticket_id, payload.draft_id)
        sent = payload.model_fields_set

        doc = _build_feedback_doc(payload, now)

        if doc_id:
            # Partial update: fields the caller did not send keep their stored values.
            doc = {k: v for k, v in doc.items() if k not in _OPTIONAL_FIELDS or k in sent}
            es_manager.client.update(index=index_name, id=doc_id, doc=doc)
            action = "updated"
        else:
            doc_id, action = f"{payload.ticket_id}_{payload.draft_id}", "created"
            es_manager.client.index(index=index_name, id=doc_id, document=doc)

        logger.info(
            "[feedback] %s resolution feedback for ticket '%s' draft '%s' → doc_id=%s index=%s",
            action.capitalize(),
            payload.ticket_id,
            payload.draft_id,
            doc_id,
            index_name,
        )
        return ResolutionFeedbackResponse(
            success=True,
            message=f"Feedback {action} successfully.",
            action=action,
            document_id=doc_id,
            index=index_name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[feedback] Failed to index resolution feedback: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store feedback: {exc}",
        ) from exc
