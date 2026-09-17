"""
app/api/dashboard/router.py
---------------------------
Dashboard analytics API endpoints.

Aggregates statistics from:
  1. `aurora_igot_user_tickets` (total tickets, category/sub-category breakdown)
  2. `aurora_resolution_feedback` (helpful vs unhelpful resolutions)
  3. Calculates the success rate based on total tickets and positive feedback (is_helpful=true).

Endpoints:
  - GET  /api/v1/dashboard/analytics?date_from=...&date_to=...
  - POST /api/v1/dashboard/analytics
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.utils.es_utils import es_manager

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class AnalyticsDateFilter(BaseModel):
    date_from: Optional[str] = Field(
        None,
        description="Filter start date. Supports YYYY-MM-DD, DD-MM-YYYY, or ISO 8601.",
        example="2026-09-01",
    )
    date_to: Optional[str] = Field(
        None,
        description="Filter end date. Supports YYYY-MM-DD, DD-MM-YYYY, or ISO 8601.",
        example="2026-09-11",
    )


class SubCategorySplit(BaseModel):
    sub_category: str
    count: int
    percentage: float


class CategorySplit(BaseModel):
    category: str
    count: int
    percentage: float
    sub_categories: List[SubCategorySplit] = []


class FeedbackSummary(BaseModel):
    helpful_tickets: int = Field(..., description="Count of unique tickets with is_helpful = true")
    unhelpful_tickets: int = Field(..., description="Count of unique tickets/feedback with is_helpful = false")
    total_feedback: int = Field(..., description="Total feedback submissions in the requested range")


class SuccessRate(BaseModel):
    percentage: float = Field(..., description="Success rate percentage: (helpful_tickets / total_tickets) * 100")
    helpful_tickets: int = Field(..., description="Number of tickets with positive feedback (is_helpful=true)")
    total_tickets: int = Field(..., description="Total tickets in aurora_igot_user_tickets for requested range")


class DashboardAnalyticsResponse(BaseModel):
    status: str = "success"
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    total_tickets: int = Field(..., description="Total number of tickets from aurora_igot_user_tickets")
    category_split: List[CategorySplit] = Field(..., description="Ticket category wise split from aurora_igot_user_tickets")
    feedback_summary: FeedbackSummary
    success_rate: SuccessRate


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_date(value: Optional[str], end_of_day: bool = False) -> Optional[str]:
    """
    Parse a user-supplied date string into UTC ISO-8601 format.
    Supports YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY, YYYY/MM/DD, and ISO-8601.
    """
    if not value or not value.strip():
        return None

    val = value.strip()
    dt: Optional[datetime] = None
    has_time = False

    # Try full ISO-8601 / datetime format with time first
    if "T" in val or " " in val:
        has_time = True
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%d-%m-%Y %H:%M:%S",
                "%d-%m-%YT%H:%M:%S",
            ):
                try:
                    dt = datetime.strptime(val, fmt)
                    break
                except ValueError:
                    continue

    # Try date-only formats
    if dt is None:
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(val, fmt)
                break
            except ValueError:
                continue

    if dt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid date format '{val}'. Expected YYYY-MM-DD or DD-MM-YYYY.",
        )

    if end_of_day and not has_time:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return dt.isoformat()


def _extract_total_hits(res: dict) -> int:
    """Extract hit count whether Elasticsearch returned an integer or a {'value': N} dict."""
    total = res.get("hits", {}).get("total", 0)
    if isinstance(total, dict):
        return int(total.get("value", 0))
    return int(total)


async def _compute_dashboard_analytics(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> DashboardAnalyticsResponse:
    """Core analytics calculation engine querying both Elasticsearch indices."""
    if not es_manager.client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Elasticsearch client is not connected.",
        )

    df = _parse_date(date_from, end_of_day=False)
    dt = _parse_date(date_to, end_of_day=True)

    user_tickets_index = getattr(es_manager, "user_tickets_index", "aurora_igot_user_tickets")
    feedback_index = getattr(es_manager, "resolution_feedback_index", "aurora_resolution_feedback")

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Query aurora_igot_user_tickets (Total tickets + Category Split)
    # ──────────────────────────────────────────────────────────────────────────
    ticket_filter = []
    if df or dt:
        date_range: Dict[str, str] = {}
        if df:
            date_range["gte"] = df
        if dt:
            date_range["lte"] = dt
        ticket_filter.append({"range": {"created_at": date_range}})

    ticket_query: Dict[str, Any] = {
        "bool": {
            "filter": ticket_filter
        }
    } if ticket_filter else {"match_all": {}}

    ticket_aggs: Dict[str, Any] = {
        "categories": {
            "terms": {"field": "category", "size": 100},
            "aggs": {
                "sub_categories": {
                    "terms": {"field": "sub_category", "size": 100}
                }
            }
        }
    }

    try:
        res_tickets = es_manager.client.search(
            index=user_tickets_index,
            query=ticket_query,
            aggs=ticket_aggs,
            size=0,
        )
    except Exception as exc:
        logger.error("[dashboard] Failed to query user tickets index '%s': %s", user_tickets_index, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error querying ticket index: {exc}",
        ) from exc

    total_tickets = _extract_total_hits(res_tickets)

    cat_buckets = res_tickets.get("aggregations", {}).get("categories", {}).get("buckets", [])
    category_split: List[CategorySplit] = []
    for cat in cat_buckets:
        cat_name = cat.get("key", "uncategorized")
        cat_count = cat.get("doc_count", 0)
        cat_pct = round((cat_count / total_tickets * 100), 2) if total_tickets > 0 else 0.0

        sub_buckets = cat.get("sub_categories", {}).get("buckets", [])
        sub_splits: List[SubCategorySplit] = [
            SubCategorySplit(
                sub_category=sub.get("key", "unknown"),
                count=sub.get("doc_count", 0),
                percentage=round((sub.get("doc_count", 0) / cat_count * 100), 2) if cat_count > 0 else 0.0,
            )
            for sub in sub_buckets
        ]

        category_split.append(
            CategorySplit(
                category=cat_name,
                count=cat_count,
                percentage=cat_pct,
                sub_categories=sub_splits,
            )
        )

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Query aurora_resolution_feedback (Helpful vs Unhelpful + Success Rate)
    # ──────────────────────────────────────────────────────────────────────────
    fb_filter = []
    if df or dt:
        date_range_fb: Dict[str, str] = {}
        if df:
            date_range_fb["gte"] = df
        if dt:
            date_range_fb["lte"] = dt

        fb_filter.append({
            "bool": {
                "should": [
                    {"range": {"ticket_created_on": date_range_fb}},
                    {"range": {"submitted_on": date_range_fb}}
                ],
                "minimum_should_match": 1
            }
        })

    fb_query: Dict[str, Any] = {
        "bool": {
            "filter": fb_filter
        }
    } if fb_filter else {"match_all": {}}

    fb_aggs: Dict[str, Any] = {
        "by_helpful": {
            "terms": {"field": "is_helpful"}
        },
        "helpful_unique_tickets": {
            "filter": {"term": {"is_helpful": True}},
            "aggs": {
                "unique_tickets": {"cardinality": {"field": "ticket_id"}}
            }
        }
    }

    try:
        res_fb = es_manager.client.search(
            index=feedback_index,
            query=fb_query,
            aggs=fb_aggs,
            size=0,
        )
    except Exception as exc:
        logger.error("[dashboard] Failed to query feedback index '%s': %s", feedback_index, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error querying feedback index: {exc}",
        ) from exc

    total_feedback = _extract_total_hits(res_fb)
    aggs_fb = res_fb.get("aggregations", {})

    # Breakdown of feedback
    helpful_fb_count = 0
    unhelpful_fb_count = 0
    for bucket in aggs_fb.get("by_helpful", {}).get("buckets", []):
        k = bucket.get("key")
        if k in (1, True, "true", "True"):
            helpful_fb_count = bucket.get("doc_count", 0)
        elif k in (0, False, "false", "False"):
            unhelpful_fb_count = bucket.get("doc_count", 0)

    # Unique helpful tickets (cardinality on ticket_id)
    unique_helpful_tickets = aggs_fb.get("helpful_unique_tickets", {}).get("unique_tickets", {}).get("value", 0)
    helpful_tickets_metric = unique_helpful_tickets if unique_helpful_tickets > 0 else helpful_fb_count

    # Success rate calculation:
    # (helpful tickets / total tickets) * 100
    success_rate_pct = round((helpful_tickets_metric / total_tickets * 100), 2) if total_tickets > 0 else 0.0

    return DashboardAnalyticsResponse(
        status="success",
        date_from=df,
        date_to=dt,
        total_tickets=total_tickets,
        category_split=category_split,
        feedback_summary=FeedbackSummary(
            helpful_tickets=helpful_tickets_metric,
            unhelpful_tickets=unhelpful_fb_count,
            total_feedback=total_feedback,
        ),
        success_rate=SuccessRate(
            percentage=success_rate_pct,
            helpful_tickets=helpful_tickets_metric,
            total_tickets=total_tickets,
        ),
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get(
    "/analytics",
    response_model=DashboardAnalyticsResponse,
    summary="Get dashboard ticket analytics and success rate (GET)",
    description=(
        "Returns total tickets, category-wise breakdown from `aurora_igot_user_tickets`, "
        "and success rate based on `aurora_resolution_feedback` for the specified date range."
    ),
)
async def get_dashboard_analytics_get(
    date_from: Optional[str] = Query(
        None,
        description="Filter start date (YYYY-MM-DD or DD-MM-YYYY)",
        example="2026-09-01",
    ),
    date_to: Optional[str] = Query(
        None,
        description="Filter end date (YYYY-MM-DD or DD-MM-YYYY)",
        example="2026-09-11",
    ),
):
    return await _compute_dashboard_analytics(date_from=date_from, date_to=date_to)


@router.post(
    "/analytics",
    response_model=DashboardAnalyticsResponse,
    summary="Get dashboard ticket analytics and success rate (POST)",
    description=(
        "POST variant allowing JSON body with `date_from` and `date_to`."
    ),
)
async def get_dashboard_analytics_post(filter_payload: AnalyticsDateFilter):
    return await _compute_dashboard_analytics(
        date_from=filter_payload.date_from,
        date_to=filter_payload.date_to,
    )
