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
  - GET  /api/v1/dashboard/overview?month=YYYY-MM&date=YYYY-MM-DD&week_index=N
"""
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from elasticsearch import helpers
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


# ── Overview (month / week / day drill-down) ──────────────────────────────────
#
# Every number is derived from the same per-ticket rows, so weeks, days,
# categories and sub-categories always add up to the month.
#
#   tickets_received → tickets in `aurora_igot_user_tickets` (by created_at, IST),
#                      excluding the "junk" category
#   drafts_created   → those tickets with status == "resolved" (AI drafted a reply)
#   drafts_accepted  → drafts whose latest feedback in
#                      `aurora_resolution_feedback` has is_helpful = true
#   drafts_rejected  → drafts whose latest feedback has is_helpful = false
#   drafts_pending   → drafts with no usable feedback yet
#                      (drafts_created - drafts_accepted - drafts_rejected)
#   success_rate     → drafts_accepted / drafts_created × 100

DASHBOARD_TZ_NAME = "Asia/Kolkata"
DASHBOARD_TZ = ZoneInfo(DASHBOARD_TZ_NAME)
DRAFT_STATUS = "resolved"
UNCATEGORIZED = "Uncategorized"
JUNK_CATEGORY = "junk"
_FEEDBACK_CHUNK = 1000


class OverviewMetrics(BaseModel):
    tickets_received: int
    drafts_created: int
    drafts_accepted: int
    drafts_rejected: int
    drafts_pending: int = Field(..., description="Drafts with no accept/reject feedback yet")
    success_rate: Optional[float] = Field(
        None, description="drafts_accepted / drafts_created × 100; null when no drafts"
    )
    draft_coverage: Optional[float] = Field(None, description="drafts_created / tickets_received × 100; null when no tickets")


class OverviewSubcategory(BaseModel):
    id: str
    name: str
    metrics: OverviewMetrics


class OverviewCategory(BaseModel):
    id: str
    name: str
    metrics: OverviewMetrics
    subcategories: List[OverviewSubcategory] = []


class OverviewMonthRange(BaseModel):
    earliest: str
    latest: str


class OverviewMeta(BaseModel):
    generated_at: str
    timezone: str
    today: str
    week_start: str = "monday"
    months: OverviewMonthRange


class OverviewSelection(BaseModel):
    month: str
    date: str
    week_index: int


class OverviewMonth(BaseModel):
    id: str
    start: str
    end: str
    through: str
    is_complete: bool
    metrics: OverviewMetrics
    categories: List[OverviewCategory]


class OverviewPreviousMonth(BaseModel):
    id: str
    metrics: OverviewMetrics


class OverviewWeek(BaseModel):
    index: int
    start: str
    end: str
    through: str
    status: str = Field(..., description="complete | in_progress | upcoming")
    metrics: Optional[OverviewMetrics]
    categories: List[OverviewCategory]


class OverviewDaily(BaseModel):
    date: str
    metrics: OverviewMetrics


class OverviewDay(BaseModel):
    date: str
    is_today: bool
    metrics: OverviewMetrics
    categories: List[OverviewCategory]


class DashboardOverviewResponse(BaseModel):
    meta: OverviewMeta
    selection: OverviewSelection
    month: OverviewMonth
    previous_month: OverviewPreviousMonth
    weeks: List[OverviewWeek]
    daily: List[OverviewDaily]
    day: OverviewDay


@dataclass
class _TicketRow:
    ticket_id: str
    day: date
    category: str
    sub_category: str
    is_draft: bool
    is_accepted: Optional[bool] = None  # None → no feedback yet


def _parse_is_helpful(value: Any) -> Optional[bool]:
    """True / False for an explicit verdict; None when missing or unrecognised."""
    if isinstance(value, str):
        value = value.strip().lower()
    if value in (True, 1, "true"):
        return True
    if value in (False, 0, "false"):
        return False
    return None


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "uncategorized"


def _parse_es_datetime(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _month_bounds(month_id: str) -> Tuple[date, date]:
    try:
        start = datetime.strptime(month_id, "%Y-%m").date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid month '{month_id}'. Expected YYYY-MM.",
        )
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, next_month - timedelta(days=1)


def _month_weeks(start: date, end: date) -> List[Tuple[int, date, date]]:
    """Monday–Sunday weeks, cut at the month's edges."""
    weeks, cur, idx = [], start, 1
    while cur <= end:
        week_end = min(cur + timedelta(days=6 - cur.weekday()), end)
        weeks.append((idx, cur, week_end))
        cur, idx = week_end + timedelta(days=1), idx + 1
    return weeks


def _pct(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator * 100, 1) if denominator else None


def _metrics(rows: Iterable[_TicketRow]) -> OverviewMetrics:
    received = created = accepted = rejected = 0
    for r in rows:
        received += 1
        if r.is_draft:
            created += 1
            accepted += r.is_accepted is True
            rejected += r.is_accepted is False
    return OverviewMetrics(
        tickets_received=received,
        drafts_created=created,
        drafts_accepted=accepted,
        drafts_rejected=rejected,
        drafts_pending=created - accepted - rejected,
        success_rate=_pct(accepted, created),
        draft_coverage=_pct(created, received),
    )


def _categories(rows: List[_TicketRow]) -> List[OverviewCategory]:
    grouped: Dict[str, Dict[str, List[_TicketRow]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        grouped[r.category][r.sub_category].append(r)

    categories: List[OverviewCategory] = []
    for cat_name, subs in grouped.items():
        cat_rows = [r for sub_rows in subs.values() for r in sub_rows]
        subcategories = [
            OverviewSubcategory(id=_slugify(sub_name), name=sub_name, metrics=_metrics(sub_rows))
            for sub_name, sub_rows in subs.items()
        ]
        subcategories.sort(key=lambda s: (-s.metrics.tickets_received, s.name))
        categories.append(
            OverviewCategory(
                id=_slugify(cat_name),
                name=cat_name,
                metrics=_metrics(cat_rows),
                subcategories=subcategories,
            )
        )
    categories.sort(key=lambda c: (-c.metrics.tickets_received, c.name))
    return categories


def _local_day_start_utc(day: date) -> str:
    return datetime.combine(day, time.min, DASHBOARD_TZ).astimezone(timezone.utc).isoformat()


def _fetch_ticket_rows(start: date, end: date) -> List[_TicketRow]:
    """Load one row per ticket created between start and end (inclusive, IST) with its feedback outcome."""
    user_tickets_index = getattr(es_manager, "user_tickets_index", "aurora_igot_user_tickets")
    feedback_index = getattr(es_manager, "resolution_feedback_index", "aurora_resolution_feedback")

    ticket_query = {
        "query": {
            "bool": {
                "filter": [{
                    "range": {
                        "created_at": {
                            "gte": _local_day_start_utc(start),
                            "lt": _local_day_start_utc(end + timedelta(days=1)),
                        }
                    }
                }],
                "must_not": [{"term": {"category": JUNK_CATEGORY}}],
            }
        },
        "_source": ["ticket_id", "created_at", "category", "sub_category", "status"],
    }

    rows: List[_TicketRow] = []
    try:
        for hit in helpers.scan(es_manager.client, index=user_tickets_index, query=ticket_query, size=1000):
            src = hit.get("_source", {})
            created = _parse_es_datetime(src.get("created_at"))
            if not created:
                continue
            rows.append(_TicketRow(
                ticket_id=str(src.get("ticket_id") or hit.get("_id")),
                day=created.astimezone(DASHBOARD_TZ).date(),
                category=(src.get("category") or "").strip() or UNCATEGORIZED,
                sub_category=(src.get("sub_category") or "").strip() or UNCATEGORIZED,
                is_draft=src.get("status") == DRAFT_STATUS,
            ))
    except Exception as exc:
        logger.error("[dashboard] Failed to scan user tickets index '%s': %s", user_tickets_index, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error querying ticket index: {exc}",
        ) from exc

    # Latest feedback per drafted ticket decides accepted vs rejected.
    draft_ids = list({r.ticket_id for r in rows if r.is_draft})
    latest: Dict[str, Tuple[datetime, Optional[bool]]] = {}
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    try:
        for i in range(0, len(draft_ids), _FEEDBACK_CHUNK):
            fb_query = {
                "query": {"bool": {"filter": [{"terms": {"ticket_id": draft_ids[i:i + _FEEDBACK_CHUNK]}}]}},
                "_source": ["ticket_id", "is_helpful", "submitted_on"],
            }
            for hit in helpers.scan(es_manager.client, index=feedback_index, query=fb_query, size=1000):
                src = hit.get("_source", {})
                tid = str(src.get("ticket_id"))
                submitted = _parse_es_datetime(src.get("submitted_on")) or epoch
                if tid not in latest or submitted >= latest[tid][0]:
                    latest[tid] = (submitted, _parse_is_helpful(src.get("is_helpful")))
    except Exception as exc:
        logger.error("[dashboard] Failed to scan feedback index '%s': %s", feedback_index, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error querying feedback index: {exc}",
        ) from exc

    for r in rows:
        if r.is_draft and r.ticket_id in latest:
            r.is_accepted = latest[r.ticket_id][1]
    return rows


def _earliest_month(fallback: str) -> str:
    user_tickets_index = getattr(es_manager, "user_tickets_index", "aurora_igot_user_tickets")
    try:
        res = es_manager.client.search(
            index=user_tickets_index,
            size=0,
            aggs={"min_created": {"min": {"field": "created_at"}}},
        )
    except Exception as exc:
        logger.warning("[dashboard] Could not read earliest ticket date: %s", exc)
        return fallback
    agg = res.get("aggregations", {}).get("min_created", {})
    earliest = _parse_es_datetime(agg.get("value_as_string"))
    if not earliest and agg.get("value") is not None:
        earliest = datetime.fromtimestamp(agg["value"] / 1000, tz=timezone.utc)
    return earliest.astimezone(DASHBOARD_TZ).strftime("%Y-%m") if earliest else fallback


def _compute_dashboard_overview(
    month: Optional[str] = None,
    selected_date: Optional[str] = None,
    week_index: Optional[int] = None,
) -> DashboardOverviewResponse:
    if not es_manager.client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Elasticsearch client is not connected.",
        )

    now = datetime.now(DASHBOARD_TZ)
    today = now.date()
    current_month = today.strftime("%Y-%m")

    month_id = month or current_month
    month_start, month_end = _month_bounds(month_id)
    if month_start > today:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Month '{month_id}' is in the future.",
        )
    through = min(month_end, today)

    if selected_date:
        try:
            day = datetime.strptime(selected_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid date '{selected_date}'. Expected YYYY-MM-DD.",
            )
        if not month_start <= day <= through:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Date must be between {month_start} and {through} for month {month_id}.",
            )
    else:
        day = through

    week_ranges = _month_weeks(month_start, month_end)
    if week_index is None:
        week_index = next(idx for idx, ws, we in week_ranges if ws <= day <= we)
    elif not 1 <= week_index <= len(week_ranges):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"week_index must be between 1 and {len(week_ranges)} for month {month_id}.",
        )

    rows = _fetch_ticket_rows(month_start, month_end)
    by_day: Dict[date, List[_TicketRow]] = defaultdict(list)
    for r in rows:
        by_day[r.day].append(r)

    def rows_between(start: date, end: date) -> List[_TicketRow]:
        return [r for r in rows if start <= r.day <= end]

    weeks: List[OverviewWeek] = []
    for idx, ws, we in week_ranges:
        if ws > today:
            week_status, week_metrics, week_categories = "upcoming", None, []
        else:
            week_rows = rows_between(ws, we)
            week_status = "complete" if we < today else "in_progress"
            week_metrics, week_categories = _metrics(week_rows), _categories(week_rows)
        weeks.append(OverviewWeek(
            index=idx,
            start=ws.isoformat(),
            end=we.isoformat(),
            through=min(we, today).isoformat(),
            status=week_status,
            metrics=week_metrics,
            categories=week_categories,
        ))

    daily: List[OverviewDaily] = []
    cur = month_start
    while cur <= through:
        daily.append(OverviewDaily(date=cur.isoformat(), metrics=_metrics(by_day.get(cur, []))))
        cur += timedelta(days=1)

    prev_start, prev_end = _month_bounds((month_start - timedelta(days=1)).strftime("%Y-%m"))
    prev_rows = _fetch_ticket_rows(prev_start, prev_end)

    day_rows = by_day.get(day, [])

    return DashboardOverviewResponse(
        meta=OverviewMeta(
            generated_at=now.replace(microsecond=0).isoformat(),
            timezone=DASHBOARD_TZ_NAME,
            today=today.isoformat(),
            months=OverviewMonthRange(earliest=_earliest_month(current_month), latest=current_month),
        ),
        selection=OverviewSelection(month=month_id, date=day.isoformat(), week_index=week_index),
        month=OverviewMonth(
            id=month_id,
            start=month_start.isoformat(),
            end=month_end.isoformat(),
            through=through.isoformat(),
            is_complete=month_end < today,
            metrics=_metrics(rows),
            categories=_categories(rows),
        ),
        previous_month=OverviewPreviousMonth(id=prev_start.strftime("%Y-%m"), metrics=_metrics(prev_rows)),
        weeks=weeks,
        daily=daily,
        day=OverviewDay(
            date=day.isoformat(),
            is_today=day == today,
            metrics=_metrics(day_rows),
            categories=_categories(day_rows),
        ),
    )


@router.get(
    "/overview",
    response_model=DashboardOverviewResponse,
    summary="Get month / week / day dashboard overview",
    description=(
        "Returns month, previous-month, weekly (Mon–Sun), daily and single-day metrics with "
        "category and sub-category breakdowns, built from `aurora_igot_user_tickets` and "
        "`aurora_resolution_feedback`. Dates are bucketed in Asia/Kolkata."
    ),
)
def get_dashboard_overview(
    month: Optional[str] = Query(None, description="Month to show (YYYY-MM). Defaults to the current month.", example="2026-10"),
    date: Optional[str] = Query(None, description="Selected day (YYYY-MM-DD) within the month. Defaults to the month's last elapsed day.", example="2026-10-16"),
    week_index: Optional[int] = Query(None, description="Selected week (1-based). Defaults to the week containing `date`."),
):
    return _compute_dashboard_overview(month=month, selected_date=date, week_index=week_index)
