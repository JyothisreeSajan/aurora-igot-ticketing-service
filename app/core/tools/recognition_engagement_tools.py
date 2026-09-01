"""
tools/recognition_engagement_tools.py
--------------------------------------
Tools used exclusively by the RecognitionEngagementSubgraph.

Covers SOP workflows from Agent_SOP_Recognition_Engagement.md:

  SOP-RE1  Karma Points Issue
           get_completed_courses    → STEP 0 (course-name fallback list)
           get_completed_events     → STEP 0 (event-name fallback list) + live-participation timing
           get_karma_course_status  → STEP 1, STEP 2/3, Edge Case 1
           get_karma_event_status   → STEP 1 credited check

  SOP-RE2  Weekly Claps Issue                       → tools TBD
  SOP-RE3  Learning Hours Issue - eHRMS             → tools TBD
  SOP-RE4  Learning Hours Issue - Shiksha Path      → tools TBD
  SOP-RE5  Learning Hours Issue - SPARROW / APAR    → tools TBD
  SOP-RE6  Leader Board Issue                       → tools TBD

Tool functions are added here as each SOP is implemented.

NOTE on `/api/karmapoints/read`: the field paths below (`result.kpList`,
`context_id`, `operation_type`, `addinfo`, `points`, `credit_date`) are taken
from the API integration doc for this SOP, which describes field *meaning*
but does not include a raw sample response. If the live response nests the
list or fields differently, `_fetch_karma_points` is the single place to fix
it — verify against a real response before relying on this in production.
"""

import json
import logging
from datetime import datetime, timezone

import requests
from langchain.tools import tool

from app.core.utils.config import IGOT_API_HOST_URL, IGOT_KEY

logger = logging.getLogger(__name__)


# ── Internal helpers ────────────────────────────────────────────────────────

def _resolve_user_id(email: str) -> str | None:
    """Resolve an email to the iGOT user_id via the private user search API."""
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"request": {"filters": {"email": email}}}
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    content = resp.json().get("result", {}).get("response", {}).get("content", [])
    return content[0].get("id") if content else None


def _parse_credit_date(value) -> datetime | None:
    """Parse a credit_date value that may be epoch seconds, epoch millis, or an ISO string."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            ts = value / 1000 if value > 10**12 else value
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    return None


def _parse_addinfo(entry: dict) -> dict:
    """addinfo is returned as a nested JSON string on each kpList entry."""
    raw = entry.get("addinfo")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _fetch_karma_points(user_id: str) -> list:
    """Fetch the user's full karma points ledger (kpList) from /api/karmapoints/read."""
    url = f"{IGOT_API_HOST_URL}/api/karmapoints/read"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": "application/json",
        "x-authenticated-userid": user_id,
        "x-authenticated-user-orgid": "igot",
    }
    payload = {"limit": 200, "offset": 9999999999999}
    resp = requests.post(url, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", {}).get("kpList") or data.get("kpList") or []


# ── SOP-RE1 STEP 0 — course / event fallback selection ─────────────────────

@tool
def get_completed_courses(email: str) -> str:
    """Fetch the user's most recently completed courses.

    Used in SOP-RE1 STEP 0 only when the user's ticket does not already name a
    course — share the returned list and ask the user to confirm which one.
    Do NOT call this if the course name is already present in the ticket message.

    Returns up to 5 most recent completions, each with course_id, course_name,
    and completed_on (used to match against the karma points ledger).
    """
    try:
        user_id = _resolve_user_id(email)
        if not user_id:
            return json.dumps({"found": False, "message": "User profile not found.",
                                "_spoc_replacements": {"{{USER_EMAIL}}": email}})

        url = f"{IGOT_API_HOST_URL}/api/course/private/v4/user/enrollment/list/{user_id}"
        headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": "application/json"}
        payload = {"request": {"retiredCoursesEnabled": True, "status": ["Completed"]}}
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        courses = resp.json().get("result", {}).get("courses", [])

        sorted_courses = sorted(courses, key=lambda c: c.get("completedOn", 0), reverse=True)[:5]
        results = [
            {
                "course_id": c.get("courseId"),
                "course_name": c.get("courseName"),
                "completed_on": c.get("completedOn"),
            }
            for c in sorted_courses
        ]
        return json.dumps({
            "email": "{{USER_EMAIL}}",
            "found": True,
            "courses": results,
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        }, indent=2)
    except Exception as e:
        logger.error(f"[recognition_engagement_tools] get_completed_courses error: {e}")
        return json.dumps({"found": False, "error": str(e),
                            "_spoc_replacements": {"{{USER_EMAIL}}": email}})


@tool
def get_completed_events(email: str) -> str:
    """Fetch the user's most recently completed events, with live-participation timing.

    Used in SOP-RE1 Flow B STEP 0 only when the user's ticket does not already
    name an event — share the returned list and ask the user to confirm which
    one. Do NOT call this if the event name is already present in the ticket
    message.

    Each returned event includes `hours_since_start` and `is_live_participation`
    (true when completion was within 4 hours of the event's start — this already
    covers "completed during the event" and completion shortly after it ends;
    treat `is_live_participation: false` as the user having missed live
    participation). `is_live_participation` is omitted when the event's start
    time is unavailable — in that case, skip the timing check and go straight
    to `get_karma_event_status`.

    Returns up to 5 most recent completed events, each with event_id, event_name,
    completed_on, and start_time.
    """
    try:
        user_id = _resolve_user_id(email)
        if not user_id:
            return json.dumps({"found": False, "message": "User profile not found.",
                                "_spoc_replacements": {"{{USER_EMAIL}}": email}})

        url = f"{IGOT_API_HOST_URL}/api/user/private/v1/events/list/{user_id}"
        headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": "application/json"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        events = resp.json().get("result", {}).get("events", [])

        completed = [e for e in events if e.get("status") == 2]
        sorted_events = sorted(completed, key=lambda e: e.get("completedOn", 0), reverse=True)[:5]

        results = []
        for e in sorted_events:
            completed_on = e.get("completedOn")
            start_time = (e.get("event") or {}).get("startDateTimeInEpoch")
            entry = {
                "event_id": e.get("contentId"),
                "event_name": (e.get("event") or {}).get("name"),
                "completed_on": completed_on,
                "start_time": start_time,
            }
            comp_dt = _parse_credit_date(completed_on)
            start_dt = _parse_credit_date(start_time)
            if comp_dt and start_dt:
                hours_since = abs((comp_dt - start_dt).total_seconds()) / 3600
                entry["hours_since_start"] = round(hours_since, 2)
                entry["is_live_participation"] = hours_since <= 4
            results.append(entry)

        return json.dumps({
            "email": "{{USER_EMAIL}}",
            "found": True,
            "events": results,
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        }, indent=2)
    except Exception as e:
        logger.error(f"[recognition_engagement_tools] get_completed_events error: {e}")
        return json.dumps({"found": False, "error": str(e),
                            "_spoc_replacements": {"{{USER_EMAIL}}": email}})


# ── SOP-RE1 STEP 1-3 / Edge Case 1 — karma points ledger checks ────────────

@tool
def get_karma_course_status(email: str, course_id: str) -> str:
    """Check karma points credit status for one course, plus the monthly eligibility rank.

    Used in SOP-RE1 Flow A STEP 1 (credited check), STEP 2 (Training Plan flag),
    STEP 3 (monthly cap), and Edge Case 1 (expected vs actual points).

    Returns:
      completion_credited, rating_credited — whether each was credited at all.
      completion_points, rating_points     — actual points credited (null if not credited).
      acbp                                 — true if this course is under a Training Plan.
      has_assessment                       — true if the course has a final assessment
                                              (used with acbp to derive the expected point
                                              value: acbp+assessment=15, acbp+no assessment=10,
                                              not acbp=5).
      course_name                          — course name from the ledger, for use in replies.
      monthly_rank                         — this course's 1-based position, by credit date,
                                              among the user's NON-Training-Plan course
                                              completions in the same calendar month (only
                                              non-Training-Plan completions are counted — a
                                              rank > 4 means this course fell outside the
                                              first-4-per-month eligibility window). Only
                                              computed when completion was credited and
                                              acbp is false; null otherwise (Training-Plan
                                              courses are not subject to the monthly cap).
    """
    try:
        user_id = _resolve_user_id(email)
        if not user_id:
            return json.dumps({"found": False, "message": "User profile not found.",
                                "_spoc_replacements": {"{{USER_EMAIL}}": email}})

        kp_list = _fetch_karma_points(user_id)

        completion_entry = next(
            (e for e in kp_list if str(e.get("context_id")) == str(course_id)
             and e.get("operation_type") == "COURSE_COMPLETION"), None)
        rating_entry = next(
            (e for e in kp_list if str(e.get("context_id")) == str(course_id)
             and e.get("operation_type") == "RATING"), None)

        completion_info = _parse_addinfo(completion_entry) if completion_entry else {}
        rating_info = _parse_addinfo(rating_entry) if rating_entry else {}

        acbp = completion_info.get("ACBP", rating_info.get("ACBP"))
        has_assessment = completion_info.get("ASSESSMENT", rating_info.get("ASSESSMENT"))
        course_name = completion_info.get("COURSENAME") or rating_info.get("COURSENAME")

        result = {
            "email": "{{USER_EMAIL}}",
            "found": True,
            "course_id": course_id,
            "course_name": course_name,
            "completion_credited": completion_entry is not None,
            "rating_credited": rating_entry is not None,
            "completion_points": completion_entry.get("points") if completion_entry else None,
            "rating_points": rating_entry.get("points") if rating_entry else None,
            "acbp": acbp,
            "has_assessment": has_assessment,
            "monthly_rank": None,
        }

        # Monthly cap only applies to non-Training-Plan completions.
        if completion_entry is not None and acbp is False:
            target_date = _parse_credit_date(completion_entry.get("credit_date"))
            if target_date:
                same_month_non_acbp = []
                for e in kp_list:
                    if e.get("operation_type") != "COURSE_COMPLETION":
                        continue
                    info = _parse_addinfo(e)
                    if info.get("ACBP") is not False:
                        continue
                    d = _parse_credit_date(e.get("credit_date"))
                    if d and d.year == target_date.year and d.month == target_date.month:
                        same_month_non_acbp.append((d, e.get("context_id")))
                same_month_non_acbp.sort(key=lambda x: x[0])
                for idx, (_, ctx_id) in enumerate(same_month_non_acbp, start=1):
                    if str(ctx_id) == str(course_id):
                        result["monthly_rank"] = idx
                        break
                result["monthly_non_training_plan_completions_this_month"] = len(same_month_non_acbp)

        result["_spoc_replacements"] = {"{{USER_EMAIL}}": email}
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"[recognition_engagement_tools] get_karma_course_status error: {e}")
        return json.dumps({"found": False, "error": str(e),
                            "_spoc_replacements": {"{{USER_EMAIL}}": email}})


@tool
def get_karma_event_status(email: str, event_id: str) -> str:
    """Check whether karma points were credited for a specific completed event.

    Used in SOP-RE1 Flow B STEP 1, after confirming live participation via
    `get_completed_events`'s `is_live_participation` field.

    Returns credited (bool) and points (actual points credited, null if not credited).
    """
    try:
        user_id = _resolve_user_id(email)
        if not user_id:
            return json.dumps({"found": False, "message": "User profile not found.",
                                "_spoc_replacements": {"{{USER_EMAIL}}": email}})

        kp_list = _fetch_karma_points(user_id)
        entry = next((e for e in kp_list if str(e.get("context_id")) == str(event_id)), None)

        return json.dumps({
            "email": "{{USER_EMAIL}}",
            "found": True,
            "event_id": event_id,
            "credited": entry is not None,
            "points": entry.get("points") if entry else None,
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        }, indent=2)
    except Exception as e:
        logger.error(f"[recognition_engagement_tools] get_karma_event_status error: {e}")
        return json.dumps({"found": False, "error": str(e),
                            "_spoc_replacements": {"{{USER_EMAIL}}": email}})


# ── Convenience list for the subgraph ─────────────────────────────────────────

def get_recognition_engagement_tools() -> list:
    """Return all tools for the RecognitionEngagementSubgraph in SOP execution order."""
    return [
        get_completed_courses,     # SOP-RE1 Flow A STEP 0
        get_completed_events,      # SOP-RE1 Flow B STEP 0 + live-participation timing
        get_karma_course_status,   # SOP-RE1 Flow A STEP 1/2/3 + Edge Case 1
        get_karma_event_status,    # SOP-RE1 Flow B STEP 1 credited check
    ]
