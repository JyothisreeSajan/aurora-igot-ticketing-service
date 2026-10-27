"""
Unit tests for GET /api/v1/dashboard/overview (app/api/dashboard/router.py).

Elasticsearch is replaced by an in-memory fake that honours the same filters the
real queries send (created_at range, junk exclusion, ticket_id terms), and the
clock is frozen at 2026-10-16 18:30 IST so results are deterministic.
"""
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app
import app.api.dashboard.router as dashboard

client = TestClient(app)

IST = ZoneInfo("Asia/Kolkata")
FROZEN_NOW = datetime(2026, 10, 16, 18, 30, tzinfo=IST)
URL = "/api/v1/dashboard/overview"
COUNT_KEYS = ["tickets_received", "drafts_created", "drafts_accepted", "drafts_rejected", "drafts_pending"]


def _utc(y, mo, d, h, mi=0) -> str:
    """IST wall-clock time → UTC ISO string, the way ticket_store stores created_at."""
    return datetime(y, mo, d, h, mi, tzinfo=IST).astimezone(timezone.utc).isoformat()


def _ticket(tid, created_at, category, sub_category, status):
    return {"ticket_id": tid, "created_at": created_at, "category": category,
            "sub_category": sub_category, "status": status}


def _feedback(tid, is_helpful, submitted_on):
    return {"ticket_id": tid, "is_helpful": is_helpful, "submitted_on": submitted_on}


TICKETS = [
    # October 2026
    _ticket("t1", _utc(2026, 10, 1, 10), "content_related_issue", "certificate_issue", "resolved"),
    _ticket("t2", _utc(2026, 10, 2, 11), "content_related_issue", "certificate_issue", "resolved"),
    _ticket("t3", _utc(2026, 10, 6, 12), "content_related_issue", "enrolment_issues", "resolved"),
    _ticket("t4", _utc(2026, 10, 7, 9), "profile_and_user_management", "profile_update", "escalated"),
    _ticket("t5", _utc(2026, 10, 13, 15), "general", "", "resolved"),
    # 00:30 IST on the 16th is still the 15th in UTC — must be bucketed as the 16th.
    _ticket("t6", _utc(2026, 10, 16, 0, 30), "ca_apar_issue", "cap_final_assessment_locked", "resolved"),
    _ticket("t7", _utc(2026, 10, 14, 10), "junk", "", "resolved"),
    _ticket("t8", _utc(2026, 10, 16, 9), "profile_and_user_management", "profile_update", "open"),
    # September 2026 — 23:30 IST on the 30th is September, not October.
    _ticket("s1", _utc(2026, 9, 30, 23, 30), "content_related_issue", "certificate_issue", "resolved"),
    _ticket("s2", _utc(2026, 9, 10, 10), "profile_and_user_management", "access_revoked", "escalated"),
]

FEEDBACK = [
    _feedback("t1", True, _utc(2026, 10, 1, 14)),
    # Deleted first, then a revised draft was sent → latest wins → accepted.
    _feedback("t2", False, _utc(2026, 10, 2, 12)),
    _feedback("t2", True, _utc(2026, 10, 2, 16)),
    # Sent first, then deleted → rejected.
    _feedback("t3", True, _utc(2026, 10, 6, 13)),
    _feedback("t3", False, _utc(2026, 10, 7, 10)),
    # t5 has no feedback → pending, not rejected.
    _feedback("t6", True, _utc(2026, 10, 16, 10)),
    _feedback("s1", True, _utc(2026, 10, 1, 9)),
]


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW.astimezone(tz) if tz else FROZEN_NOW.replace(tzinfo=None)


def _fake_scan(_client, index, query, size=1000, **_):
    """Mimic helpers.scan for the two indices, applying the filters the router sends."""
    bool_q = query["query"]["bool"]
    if index == dashboard.es_manager.user_tickets_index:
        rng = bool_q["filter"][0]["range"]["created_at"]
        gte, lt = datetime.fromisoformat(rng["gte"]), datetime.fromisoformat(rng["lt"])
        excluded = {c["term"]["category"] for c in bool_q.get("must_not", [])}
        for t in TICKETS:
            if gte <= datetime.fromisoformat(t["created_at"]) < lt and t["category"] not in excluded:
                yield {"_id": t["ticket_id"], "_source": t}
    else:
        ids = set(bool_q["filter"][0]["terms"]["ticket_id"])
        for f in FEEDBACK:
            if f["ticket_id"] in ids:
                yield {"_id": f"fb-{f['ticket_id']}", "_source": f}


@pytest.fixture
def fake_es():
    es = MagicMock()
    es.search.return_value = {"aggregations": {"min_created": {"value_as_string": "2024-11-05T04:30:00.000Z"}}}
    with patch.object(dashboard.es_manager, "client", es), \
         patch.object(dashboard.helpers, "scan", side_effect=_fake_scan) as scan, \
         patch.object(dashboard, "datetime", _FrozenDatetime):
        yield es, scan


def _counts(metrics):
    return {k: metrics[k] for k in COUNT_KEYS}


def _sum(metrics_list):
    return {k: sum(m[k] for m in metrics_list if m) for k in COUNT_KEYS}


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_month_weeks_are_monday_to_sunday_cut_at_month_edges():
    weeks = dashboard._month_weeks(date(2026, 10, 1), date(2026, 10, 31))
    assert [(i, s.isoformat(), e.isoformat()) for i, s, e in weeks] == [
        (1, "2026-10-01", "2026-10-04"),
        (2, "2026-10-05", "2026-10-11"),
        (3, "2026-10-12", "2026-10-18"),
        (4, "2026-10-19", "2026-10-25"),
        (5, "2026-10-26", "2026-10-31"),
    ]


def test_month_bounds_handles_february_and_rejects_bad_input():
    assert dashboard._month_bounds("2028-02") == (date(2028, 2, 1), date(2028, 2, 29))
    assert dashboard._month_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    with pytest.raises(HTTPException) as exc:
        dashboard._month_bounds("2026-13")
    assert exc.value.status_code == 400


def test_pct_rounds_to_one_decimal_and_is_null_without_denominator():
    assert dashboard._pct(806, 1039) == 77.6
    assert dashboard._pct(1, 3) == 33.3
    assert dashboard._pct(0, 0) is None


def test_slugify():
    assert dashboard._slugify("Billing & Payments") == "billing-payments"
    assert dashboard._slugify("content_related_issue") == "content-related-issue"
    assert dashboard._slugify("***") == "uncategorized"


def test_metrics_counts_only_drafts_for_acceptance():
    rows = [
        dashboard._TicketRow("a", date(2026, 10, 1), "c", "s", is_draft=True, is_accepted=True),
        dashboard._TicketRow("b", date(2026, 10, 1), "c", "s", is_draft=True, is_accepted=False),
        dashboard._TicketRow("c", date(2026, 10, 1), "c", "s", is_draft=True),
        dashboard._TicketRow("d", date(2026, 10, 1), "c", "s", is_draft=False),
    ]
    m = dashboard._metrics(rows)
    assert (m.tickets_received, m.drafts_created, m.drafts_accepted, m.drafts_rejected, m.drafts_pending) == \
        (4, 3, 1, 1, 1)
    assert m.success_rate == 50.0      # 1 / (1 + 1) — pending drafts are excluded
    assert m.draft_coverage == 75.0


def test_metrics_drafts_without_feedback_are_pending_not_rejected():
    rows = [dashboard._TicketRow(t, date(2026, 10, 1), "c", "s", is_draft=True) for t in "abc"]
    m = dashboard._metrics(rows)
    assert (m.drafts_created, m.drafts_accepted, m.drafts_rejected, m.drafts_pending) == (3, 0, 0, 3)
    assert m.success_rate is None


@pytest.mark.parametrize("value, expected", [
    (True, True), (1, True), ("true", True), ("True", True),
    (False, False), (0, False), ("false", False), ("False", False),
    (None, None), ("", None), ("yes", None),
])
def test_parse_is_helpful(value, expected):
    assert dashboard._parse_is_helpful(value) is expected


def test_metrics_empty_has_null_rates():
    m = dashboard._metrics([])
    assert m.tickets_received == 0
    assert m.success_rate is None
    assert m.draft_coverage is None


# ── Endpoint ──────────────────────────────────────────────────────────────────

def test_overview_defaults_to_current_month(fake_es):
    res = client.get(URL)
    assert res.status_code == 200
    body = res.json()

    assert body["meta"]["today"] == "2026-10-16"
    assert body["meta"]["timezone"] == "Asia/Kolkata"
    assert body["meta"]["week_start"] == "monday"
    assert body["meta"]["months"] == {"earliest": "2024-11", "latest": "2026-10"}
    assert body["selection"] == {"month": "2026-10", "date": "2026-10-16", "week_index": 3}

    month = body["month"]
    assert (month["id"], month["start"], month["end"], month["through"]) == \
        ("2026-10", "2026-10-01", "2026-10-31", "2026-10-16")
    assert month["is_complete"] is False
    assert month["metrics"] == {
        "tickets_received": 7, "drafts_created": 5, "drafts_accepted": 3, "drafts_rejected": 1,
        "drafts_pending": 1, "success_rate": 75.0, "draft_coverage": 71.4,
    }


def test_overview_excludes_junk(fake_es):
    body = client.get(URL).json()
    ids = [c["id"] for c in body["month"]["categories"]]
    assert "junk" not in ids
    week3 = body["weeks"][2]
    assert week3["metrics"]["tickets_received"] == 3  # t5, t6, t8 — t7 (junk) excluded


def test_overview_latest_feedback_decides_and_missing_feedback_is_pending(fake_es):
    cats = {c["name"]: c for c in client.get(URL).json()["month"]["categories"]}
    subs = {s["name"]: s["metrics"] for s in cats["content_related_issue"]["subcategories"]}
    assert subs["certificate_issue"]["drafts_accepted"] == 2   # t1, t2 (deleted then sent)
    assert subs["enrolment_issues"]["drafts_rejected"] == 1    # t3 (sent then deleted)
    general = cats["general"]["metrics"]                        # t5 has no feedback
    assert (general["drafts_rejected"], general["drafts_pending"]) == (0, 1)
    assert general["success_rate"] is None


def test_overview_categories_sorted_with_uncategorized_subcategory(fake_es):
    cats = client.get(URL).json()["month"]["categories"]
    assert [(c["name"], c["metrics"]["tickets_received"]) for c in cats] == [
        ("content_related_issue", 3),
        ("profile_and_user_management", 2),
        ("ca_apar_issue", 1),
        ("general", 1),
    ]
    assert cats[0]["id"] == "content-related-issue"
    general = cats[3]
    assert [s["name"] for s in general["subcategories"]] == ["Uncategorized"]


def test_overview_weeks_status_and_upcoming_shape(fake_es):
    weeks = client.get(URL).json()["weeks"]
    assert [(w["index"], w["status"], w["through"]) for w in weeks] == [
        (1, "complete", "2026-10-04"),
        (2, "complete", "2026-10-11"),
        (3, "in_progress", "2026-10-16"),
        (4, "upcoming", "2026-10-16"),
        (5, "upcoming", "2026-10-16"),
    ]
    for w in weeks[3:]:
        assert w["metrics"] is None
        assert w["categories"] == []


def test_overview_daily_stops_at_today_and_uses_ist_dates(fake_es):
    body = client.get(URL).json()
    daily = body["daily"]
    assert [d["date"] for d in daily][0] == "2026-10-01"
    assert [d["date"] for d in daily][-1] == "2026-10-16"
    assert len(daily) == 16
    by_date = {d["date"]: d["metrics"] for d in daily}
    assert by_date["2026-10-16"]["tickets_received"] == 2   # t6 (00:30 IST) + t8
    assert by_date["2026-10-15"]["tickets_received"] == 0

    day = body["day"]
    assert day["date"] == "2026-10-16" and day["is_today"] is True
    assert _counts(day["metrics"]) == {
        "tickets_received": 2, "drafts_created": 1, "drafts_accepted": 1, "drafts_rejected": 0,
        "drafts_pending": 0,
    }
    assert {c["name"] for c in day["categories"]} == {"ca_apar_issue", "profile_and_user_management"}


def test_overview_previous_month(fake_es):
    prev = client.get(URL).json()["previous_month"]
    assert prev["id"] == "2026-09"
    assert prev["metrics"] == {
        "tickets_received": 2, "drafts_created": 1, "drafts_accepted": 1, "drafts_rejected": 0,
        "drafts_pending": 0, "success_rate": 100.0, "draft_coverage": 50.0,
    }


def test_overview_totals_are_consistent(fake_es):
    body = client.get(URL).json()
    month = _counts(body["month"]["metrics"])
    assert _sum([w["metrics"] for w in body["weeks"]]) == month
    assert _sum([d["metrics"] for d in body["daily"]]) == month
    assert _sum([c["metrics"] for c in body["month"]["categories"]]) == month
    for cat in body["month"]["categories"]:
        assert _sum([s["metrics"] for s in cat["subcategories"]]) == _counts(cat["metrics"])
    for week in body["weeks"]:
        if week["metrics"]:
            assert _sum([c["metrics"] for c in week["categories"]]) == _counts(week["metrics"])


def test_overview_past_month_with_selected_date_and_week(fake_es):
    res = client.get(URL, params={"month": "2026-09", "date": "2026-09-10", "week_index": 4})
    assert res.status_code == 200
    body = res.json()
    assert body["selection"] == {"month": "2026-09", "date": "2026-09-10", "week_index": 4}
    assert body["month"]["is_complete"] is True
    assert body["month"]["through"] == "2026-09-30"
    assert all(w["status"] == "complete" for w in body["weeks"])
    assert len(body["daily"]) == 30
    assert body["day"]["is_today"] is False
    assert body["day"]["metrics"]["tickets_received"] == 1   # s2
    assert body["previous_month"]["id"] == "2026-08"


def test_overview_default_week_follows_selected_date(fake_es):
    body = client.get(URL, params={"date": "2026-10-03"}).json()
    assert body["selection"]["week_index"] == 1


@pytest.mark.parametrize("params, detail", [
    ({"month": "2026-13"}, "Invalid month"),
    ({"month": "10-2026"}, "Invalid month"),
    ({"month": "2026-11"}, "in the future"),
    ({"date": "16-10-2026"}, "Invalid date"),
    ({"date": "2026-10-20"}, "Date must be between"),
    ({"month": "2026-09", "date": "2026-10-01"}, "Date must be between"),
    ({"week_index": 0}, "week_index must be between"),
    ({"week_index": 6}, "week_index must be between"),
])
def test_overview_validation_errors(fake_es, params, detail):
    res = client.get(URL, params=params)
    assert res.status_code == 400
    assert detail in res.json()["detail"]


def test_overview_earliest_month_falls_back_when_aggregation_fails(fake_es):
    es, _ = fake_es
    es.search.side_effect = RuntimeError("boom")
    body = client.get(URL).json()
    assert body["meta"]["months"]["earliest"] == "2026-10"


def test_overview_returns_503_without_es_client():
    with patch.object(dashboard.es_manager, "client", None):
        res = client.get(URL)
    assert res.status_code == 503


def test_overview_returns_500_when_ticket_scan_fails(fake_es):
    _, scan = fake_es
    scan.side_effect = RuntimeError("connection reset")
    res = client.get(URL)
    assert res.status_code == 500
    assert "Error querying ticket index" in res.json()["detail"]
