"""
app/api/feedback/router.py
--------------------------
POST endpoint to collect resolution feedback from L1 support agents or users.

Records are stored in the `aurora_resolution_feedback` Elasticsearch index
which is auto-created (with explicit mapping) when the application starts via
ESManager._ensure_indices().

Endpoint
--------
POST /api/v1/feedback/resolution
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

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


class ResolutionFeedbackResponse(BaseModel):
    """Response returned after successfully storing feedback."""

    success: bool
    message: str
    document_id: Optional[str] = None
    index: str


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post(
    "/resolution",
    response_model=ResolutionFeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit resolution feedback",
    description=(
        "Stores L1 support / user feedback for a resolved ticket into the "
        "`aurora_resolution_feedback` Elasticsearch index."
    ),
)
async def submit_resolution_feedback(payload: ResolutionFeedbackRequest):
    """
    Accept resolution feedback and persist it to ElasticSearch.

    Raises
    ------
    503  Service Unavailable – when the ES client is not connected.
    500  Internal Server Error – on unexpected indexing failures.
    """
    if not es_manager.client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ElasticSearch is not available. Feedback cannot be stored.",
        )

    now = datetime.now(timezone.utc)

    doc = {
        "ticket_id":         payload.ticket_id,
        "is_helpful":        payload.is_helpful,
        "category":          payload.category or "",
        "comments":          payload.comments or "",
        "submitted_on":      (payload.submitted_on or now).isoformat(),
        "ticket_created_on": payload.ticket_created_on.isoformat() if payload.ticket_created_on else None,
        "l1_support_team":   payload.l1_support_team or "",
    }

    try:
        index_name = es_manager.resolution_feedback_index
        result = es_manager.client.index(index=index_name, document=doc)
        doc_id = result.get("_id")
        logger.info(
            "[feedback] Stored resolution feedback for ticket '%s' → doc_id=%s index=%s",
            payload.ticket_id,
            doc_id,
            index_name,
        )
        return ResolutionFeedbackResponse(
            success=True,
            message="Feedback submitted successfully.",
            document_id=doc_id,
            index=index_name,
        )
    except Exception as exc:
        logger.error("[feedback] Failed to index resolution feedback: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store feedback: {exc}",
        ) from exc
