"""
tools/certificate_tools.py
---------------------------
Tools for two distinct content_related_issue sub-categories:

  certificate_not_received (UC-03 Certificate Not Generated flow — courses
  AND programs; events out of scope): a completed/in-progress course or
  program's certificate has not been received/generated. Wording branches on
  primary_category ("course" vs "program") but the diagnosis logic is
  identical for both — no Hierarchy Read / Admin Content State cross-check
  needed for this flow.
    get_user_enrollments          → STEP 1
    diagnose_certificate_receipt  → STEP 1.5 + STEP 2 (content read,
                                     completion / issuedCertificates / 24h
                                     timing, pending-resource + SCORM
                                     diagnosis)

  certificate_issue (SOP-03): incorrect name on an already-generated
  certificate.
    get_user_details → STEP 1  (maps to get_user_profile in SOP)
"""

import json
import logging
from datetime import datetime, timezone

import requests
from langchain.tools import tool

from app.core.utils.config import IGOT_API_HOST_URL, IGOT_KEY

logger = logging.getLogger(__name__)

# ── Shared field filter for enrollment responses ───────────────────────────────

ENROLLMENT_FIELDS = [
    "enrolledDate",
    "contentId",
    "contentStatus",
    "certstatus",
    "courseId",
    "collectionId",
    "active",
    "userId",
    "completionPercentage",
    "issuedCertificates",
    "courseName",
    "certificates",
    "completedOn",
    "progress",
    "status",
]


# ── Internal helper ────────────────────────────────────────────────────────────

def _get_latest_enrollments(
    user_id: str,
    status: list,
    top_n: int = 20,
    fields: list = ENROLLMENT_FIELDS,
) -> dict:
    """Internal helper: fetch and filter enrollment list by user_id."""
    url = f"{IGOT_API_HOST_URL}/api/course/private/v4/user/enrollment/list/{user_id}"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"request": {"status": status}}

    response = requests.post(url, headers=headers, json=payload, timeout=10)
    response.raise_for_status()
    full_response = response.json()

    courses = full_response.get("result", {}).get("courses", [])

    sorted_courses = sorted(
        courses,
        key=lambda c: c.get("enrolledDate") or 0,
        reverse=True,
    )
    top_courses = sorted_courses[:top_n]
    filtered_courses = [
        {key: course[key] for key in fields if key in course}
        for course in top_courses
    ]

    return {
        "responseCode": full_response.get("responseCode"),
        "total_fetched": len(courses),
        "returned": len(filtered_courses),
        "result": {"courses": filtered_courses},
    }


# ── SOP-01 / SOP-02 ───────────────────────────────────────────────────────────

@tool
def get_user_enrollments(email: str, status_filter: str | None = None) -> str:
    """Fetch all courses/programs a user is enrolled in.

    Used in SOP-01 STEP 2 and SOP-02 STEP 2 to retrieve enrollment and
    completion data before diagnosing certificate issues.

    status_filter can be 'In-Progress' or 'Completed'. If not passed, fetches both.
    """
    try:
        # Resolve email → user_id
        url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
        headers = {
            "Authorization": f"Bearer {IGOT_KEY}",
            "Content-Type": "application/json",
        }
        payload = {"request": {"filters": {"email": email}}}

        search_resp = requests.post(url, json=payload, headers=headers, timeout=10)
        search_resp.raise_for_status()
        search_data = search_resp.json()
        content = search_data.get("result", {}).get("response", {}).get("content", [])

        if not content:
            return json.dumps({"error": "User not found.", "_spoc_replacements": {"{{USER_EMAIL}}": email}})
        user_id = content[0].get("id")
        if not user_id:
            return json.dumps({"error": "User found but user_id is empty.", "_spoc_replacements": {"{{USER_EMAIL}}": email}})

        api_status = ["In-Progress", "Completed"]
        if status_filter == "In-Progress":
            api_status = ["In-Progress"]
        elif status_filter == "Completed":
            api_status = ["Completed"]

        enrollments = _get_latest_enrollments(user_id=user_id, status=api_status)
        logger.debug(f"[certificate_tools] Enrollments fetched for user_id={user_id}")
        return json.dumps({
            "email": "{{USER_EMAIL}}",
            **enrollments,
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        }, indent=2)
    except Exception as e:
        logger.error(f"[certificate_tools] Error fetching enrollments: {e}")
        return json.dumps({"error": f"Error fetching enrollments: {e!s}", "_spoc_replacements": {"{{USER_EMAIL}}": email}})


# ── SOP-02 diagnosis helpers ────────────────────────────────────────────────────

def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _flatten_lang_content_status(lang_content_status: dict) -> dict:
    """{lang: {resource_id: status}} -> {resource_id: max_status_across_langs}."""
    flat: dict = {}
    for _lang, resources in (lang_content_status or {}).items():
        for rid, status in (resources or {}).items():
            flat[rid] = max(flat.get(rid, 0), _as_int(status))
    return flat


def _fetch_content(content_id: str) -> dict:
    """GET /api/extended/content/v1/read/{content_id} -> result.content."""
    url = f"{IGOT_API_HOST_URL}/api/extended/content/v1/read/{content_id}"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": "application/json"}
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("content", {}) or {}


_SCORM_MIME_TYPE = "application/vnd.ekstep.html-archive"


def _fetch_composite_metadata(identifiers: list) -> list:
    """POST /api/composite/v4/search -> content[] metadata (name, mimeType) for pending resources."""
    url = f"{IGOT_API_HOST_URL}/api/composite/v4/search"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": "application/json"}
    payload = {
        "request": {
            "filters": {"identifier": identifiers, "status": ["Live", "Review", "Draft", "Retired"]},
            "isSecureSettingsDisabled": True,
            "sort_by": {"createdOn": "desc"},
            "fields": ["identifier", "name", "mimeType", "status", "duration"],
            "facets": ["status"],
            "limit": 1000,
        }
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("content", []) or []


def _resolve_pending_resources_metadata(incomplete_ids: list) -> dict:
    """Name + SCORM-detection lookup for pending resource ids — composite search
    first, falling back to a per-id content read for anything it missed (or for
    every id, if the composite search call fails outright)."""
    try:
        found = _fetch_composite_metadata(incomplete_ids)
        found_by_id = {item.get("identifier"): item for item in found if item.get("identifier")}
    except Exception as e:
        logger.warning(f"[certificate_tools] composite metadata search failed, falling back per-id: {e}")
        found_by_id = {}

    for rid in [rid for rid in incomplete_ids if rid not in found_by_id]:
        try:
            content = _fetch_content(rid)
            if content:
                found_by_id[rid] = content
        except Exception as e:
            logger.warning(f"[certificate_tools] fallback content read failed for {rid}: {e}")

    items = [found_by_id[rid] for rid in incomplete_ids if rid in found_by_id]
    scorm_items = [item for item in items if item.get("mimeType") == _SCORM_MIME_TYPE]

    return {
        "pending_resource_names": [item.get("name") for item in items if item.get("name")],
        "has_scorm_resources": bool(scorm_items),
        "scorm_resource_name": scorm_items[0].get("name") if scorm_items else None,
    }


def _parse_completed_on(value) -> datetime | None:
    """Parse a completedOn value that may be epoch seconds, epoch millis, or an ISO string."""
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


def _hours_since_completion(completed_on) -> float | None:
    """Hours elapsed between completed_on and now. None when completed_on is
    missing/unparsable — callers must treat that the same as >24 hours, per SOP."""
    parsed = _parse_completed_on(completed_on)
    if parsed is None:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 3600


@tool
def diagnose_certificate_receipt(course_id: str, email: str) -> str:
    """Run the certificate-not-received diagnosis (UC-03) for one course or
    program the user is enrolled in. Call get_user_enrollments first to find
    course_id — never guess it.

    Returns one `status` (plus course_name and primary_category — phrase your
    response using "course" or "program" wording based on primary_category):
      "not_enrolled"         — no enrollment record found. Close, no ticket.
      "not_started"          — enrollment status=0; guide the user to start it.
      "resources_pending"    — in progress with genuinely incomplete resources;
                                see pending_resource_names, has_scorm_resources,
                                scorm_resource_name.
      "completed_over_24h"   — certificate available: issuedCertificates is
                                already populated, OR completedOn is missing/
                                more than 24h ago (wait guard), OR status=1 but
                                no incomplete resources remain (sync/cache lag).
                                Guide the user to (re-)download; only escalate
                                if a follow-up ticket says they still can't.
      "completed_under_24h"  — status=2, issuedCertificates empty, and
                                completedOn is within the last 24 hours —
                                certificate not yet generated. See
                                hours_remaining.
      "error"                 — something failed; treat as unknown, do not guess.
    """
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": "application/json"}
    payload = {"request": {"filters": {"email": email}}}
    try:
        search_resp = requests.post(url, json=payload, headers=headers, timeout=10)
        search_resp.raise_for_status()
        content = search_resp.json().get("result", {}).get("response", {}).get("content", [])
        if not content or not content[0].get("id"):
            return json.dumps({"status": "error", "message": "User not found."})
        user_id = content[0]["id"]

        enroll_url = f"{IGOT_API_HOST_URL}/api/course/private/v4/user/enrollment/list/{user_id}"
        enroll_payload = {"request": {"retiredCoursesEnabled": True, "status": ["In-Progress", "Completed"]}}
        enroll_resp = requests.post(enroll_url, json=enroll_payload, headers=headers, timeout=10)
        enroll_resp.raise_for_status()
        courses = enroll_resp.json().get("result", {}).get("courses", []) or []

        course = next((c for c in courses if c.get("courseId") == course_id), None)
        if not course:
            return json.dumps({"status": "not_enrolled", "message": "No enrollment record found for this course/program."})

        course_name = course.get("courseName")

        # STEP 1.5 — always read content, regardless of status: leafNodes for the
        # incomplete-resource diff, primaryCategory for course/program wording.
        leaf_content = _fetch_content(course_id)
        primary_category = leaf_content.get("primaryCategory")
        leaf_nodes = leaf_content.get("leafNodes") or []

        status = _as_int(course.get("status"))

        if status == 0:
            return json.dumps({"status": "not_started", "course_name": course_name, "primary_category": primary_category})

        if status == 2:
            if course.get("issuedCertificates"):
                return json.dumps({
                    "status": "completed_over_24h", "course_name": course_name,
                    "primary_category": primary_category, "completed_on": course.get("completedOn"),
                })
            completed_on = course.get("completedOn")
            hours = _hours_since_completion(completed_on)
            if hours is not None and hours <= 24:
                return json.dumps({
                    "status": "completed_under_24h", "course_name": course_name,
                    "primary_category": primary_category, "completed_on": completed_on,
                    "hours_remaining": round(24 - hours, 1),
                })
            return json.dumps({
                "status": "completed_over_24h", "course_name": course_name,
                "primary_category": primary_category, "completed_on": completed_on,
            })

        # status == 1 (in progress), or any other unexpected value — diff leaf
        # nodes against completed ids to find what's actually still pending.
        flat = _flatten_lang_content_status(course.get("langContentStatus") or {})
        completed_ids = {rid for rid, s in flat.items() if s == 2}
        incomplete_ids = [rid for rid in leaf_nodes if rid not in completed_ids]

        if not incomplete_ids:
            # Sync/cache lag: portal says in-progress but nothing is actually
            # pending — treat the same as "certificate available".
            return json.dumps({
                "status": "completed_over_24h", "course_name": course_name,
                "primary_category": primary_category, "completed_on": course.get("completedOn"),
            })

        meta = _resolve_pending_resources_metadata(incomplete_ids)
        return json.dumps({
            "status": "resources_pending",
            "course_name": course_name,
            "primary_category": primary_category,
            **meta,
        })
    except Exception as e:
        logger.error(f"[certificate_tools] diagnose_certificate_receipt error: {e}")
        return json.dumps({"status": "error", "error": str(e)})


# ── SOP-03 ────────────────────────────────────────────────────────────────────

@tool
def get_user_details(email: str) -> str:
    """Fetch user profile details including full name, organization, and designation.

    Used in SOP-03 STEP 1 (maps to get_user_profile in the SOP).
    The firstName + lastName fields are the authoritative source for the name
    that appears on all generated certificates.
    """
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"request": {"filters": {"email": email}}}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        content = data.get("result", {}).get("response", {}).get("content", [])
        if not content:
            return json.dumps({"error": "User details not found.", "_spoc_replacements": {"{{USER_EMAIL}}": email}})

        user = content[0]

        prof_details = {}
        prof_list = user.get("profileDetails", {}).get("professionalDetails")
        if isinstance(prof_list, list) and len(prof_list) > 0:
            prof_details = prof_list[0]

        filtered_user = {
            "email": "{{USER_EMAIL}}",
            "rootOrgName": user.get("rootOrgName"),
            "channel": user.get("channel"),
            "language": user.get("language"),
            "id": user.get("id"),
            "rootOrgId": user.get("rootOrgId"),
            "firstName": user.get("firstName"),
            "lastName": user.get("lastName"),
            "profileDetails": {
                "professionalDetails": [
                    {
                        "verifiedKarmayogi": prof_details.get("verifiedKarmayogi"),
                        "profileStatus": prof_details.get("profileStatus"),
                        "designation": prof_details.get("designation"),
                    }
                ]
            },
            "userType": user.get("userType"),
            "status": user.get("status"),
            "gender": user.get("gender"),
            "roles": user.get("roles", []),
            "phoneVerified": user.get("phoneVerified"),
            "userName": user.get("userName"),
            "emailVerified": user.get("emailVerified"),
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        }

        return json.dumps(filtered_user, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Error fetching user details: {e!s}", "_spoc_replacements": {"{{USER_EMAIL}}": email}})


# ── Shared — ticket escalation ─────────────────────────────────────────────────



# ── Convenience lists for the subgraph ─────────────────────────────────────────

def get_certificate_not_received_tools() -> list:
    """Return all tools for the certificate_not_received (SOP-02) flow."""
    return [
        get_user_enrollments,          # STEP 2
        diagnose_certificate_receipt,  # STEPS 4-6
    ]
