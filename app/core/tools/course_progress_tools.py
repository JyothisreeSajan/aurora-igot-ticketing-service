"""
tools/course_progress_tools.py
-------------------------------
Tools for content_related_issue -> course_program_progress_issue: course/program
progress not updating or certificate not generated.

Built from the UC-01 Course / Program Progress Issue API Integration Guide
(steps 1-7 plus the R1/R2 revalidation pass). Events are out of scope for this
leaf — see COURSE_PROGRESS_SYSTEM_PROMPT.

Endpoints (IGOT_API_HOST_URL = https://portal.uat.karmayogibharat.net in UAT):
  POST /api/private/user/v1/search                     — email -> user_id
  POST /api/course/private/v4/user/enrollment/list/{id} — enrollment list (picker + diff source)
  GET  /api/extended/content/v1/read/{content_id}       — primaryCategory / leafNodes
  GET  /api/private/content/v3/hierarchy/{program_id}   — child Course DO_IDs (Programs only)
  POST /api/admin/content/state/read                    — backend per-resource completion state
  POST /api/composite/v4/search                         — pending-resource metadata
  GET  /api/admin/assesment/retake/count                — remaining assessment attempts

The sync-mismatch / completion / leaf-node-diff comparisons are computed here,
deterministically, rather than left to the LLM to reason about raw JSON —
matching the pattern used in enrolment_tools.py.
"""

import json
import logging

import requests
from langchain.tools import tool

from app.core.utils.config import IGOT_API_HOST_URL, IGOT_KEY

logger = logging.getLogger(__name__)

_HEADERS_JSON = {
    "Authorization": f"Bearer {IGOT_KEY}",
    "Content-Type": "application/json",
}

USER_NOT_FOUND_MESSAGE = "User profile not found."

_SCORM_MIME_TYPE = "application/vnd.ekstep.html-archive"
_ASSESSMENT_CATEGORY = "Course Assessment"
_PROGRAM_CATEGORIES = ("Program", "Curated Program")


# ── Internal helpers ────────────────────────────────────────────────────────────

def _fetch_user_record(email: str) -> dict | None:
    """POST /api/private/user/v1/search by email -> first matching user record."""
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    resp = requests.post(url, json={"request": {"filters": {"email": email}}}, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    content = resp.json().get("result", {}).get("response", {}).get("content", [])
    return content[0] if content else None


def _fetch_enrollments(user_id: str, status: list) -> list:
    """POST enrollment/list/{user_id} -> courses[], filtered by status."""
    url = f"{IGOT_API_HOST_URL}/api/course/private/v4/user/enrollment/list/{user_id}"
    payload = {"request": {"retiredCoursesEnabled": True, "status": status}}
    resp = requests.post(url, json=payload, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("courses", []) or []


def _extract_batch_id(course: dict) -> str | None:
    """Prefer courses[].batchId; fall back to the nested batches[0].batchId
    (in-progress courses sometimes carry the batch ref there instead)."""
    batch_id = course.get("batchId")
    if batch_id and batch_id != "NONE":
        return batch_id
    batches = course.get("batches") or []
    if isinstance(batches, list) and batches:
        return batches[0].get("batchId")
    return None


def _has_issued_certificate(course: dict) -> bool:
    return bool(course.get("issuedCertificates"))


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
    resp = requests.get(url, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("content", {}) or {}


def _fetch_hierarchy_child_course_ids(program_id: str) -> list:
    """GET /api/private/content/v3/hierarchy/{program_id} -> child Course DO_IDs."""
    url = f"{IGOT_API_HOST_URL}/api/private/content/v3/hierarchy/{program_id}"
    resp = requests.get(url, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    children = resp.json().get("result", {}).get("content", {}).get("children", []) or []
    return [c.get("identifier") for c in children if c.get("identifier")]


def _fetch_admin_content_state(user_id: str, course_id: str, batch_id: str | None) -> list:
    """POST /api/admin/content/state/read -> consumptionRecords[]."""
    url = f"{IGOT_API_HOST_URL}/api/admin/content/state/read"
    payload = {"request": {"userId": user_id, "courseId": course_id, "batchId": batch_id}}
    resp = requests.post(url, json=payload, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    return resp.json().get("consumptionRecords", []) or []


def _detect_technical_issue(lang_content_status: dict, admin_records: list) -> bool:
    """True if any resource shows enrollment status=1 (In-Progress on the portal)
    but the backend admin record for the same resource shows status=2 (Completed)
    — a sync mismatch between the portal and the backend."""
    enrollment_status = _flatten_lang_content_status(lang_content_status)
    for record in admin_records:
        rid = record.get("contentid")
        if enrollment_status.get(rid) == 1 and _as_int(record.get("status")) == 2:
            return True
    return False


_COMPOSITE_FIELDS = ["identifier", "name", "mimeType", "status", "duration",
                     "primaryCategory", "maxAttempts", "maxAssessmentRetakeAttempts"]


def _fetch_composite_metadata(
    identifiers: list,
    fields: list = _COMPOSITE_FIELDS,
    filter_status: bool = True,
    sort_by_created: bool = True,
) -> list:
    """POST /api/composite/v4/search -> content[] metadata for the given resource ids.
    Shared across course_progress_tools.py, certificate_tools.py, and
    content_resource_tools.py — each passes its own `fields` subset."""
    url = f"{IGOT_API_HOST_URL}/api/composite/v4/search"
    request_body = {
        "filters": {"identifier": identifiers},
        "isSecureSettingsDisabled": True,
        "fields": fields,
        "limit": 1000,
    }
    if filter_status:
        request_body["filters"]["status"] = ["Live", "Review", "Draft", "Retired"]
        request_body["facets"] = ["status"]
    if sort_by_created:
        request_body["sort_by"] = {"createdOn": "desc"}

    resp = requests.post(url, json={"request": request_body}, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("content", []) or []


# ── STEP 0: enrolled course/program picker ──────────────────────────────────────

@tool
def get_user_course_enrollments(email: str) -> str:
    """Fetch the user's enrolled courses/programs (In-Progress and Completed) so
    the ticket's course/program name can be fuzzy-matched against this list.

    Returns found, count, and courses: [{course_id, course_name, completion_pct,
    primary_category, certificate_issued, enrolled}]. Pass the matched course_id
    into diagnose_course_progress next — never guess a course_id.
    """
    try:
        user = _fetch_user_record(email)
        if not user or not user.get("id"):
            return json.dumps({"found": False, "message": USER_NOT_FOUND_MESSAGE})
        user_id = user["id"]

        courses = _fetch_enrollments(user_id, ["In-Progress", "Completed"])
        if not courses:
            return json.dumps({"found": False, "message": "No enrollments found for this user."})

        results = [
            {
                "course_id":        c.get("courseId"),
                "course_name":      c.get("courseName"),
                "completion_pct":   c.get("completionPercentage"),
                "primary_category": c.get("primaryCategory"),
                "certificate_issued": _has_issued_certificate(c),
                "enrolled":          bool(_extract_batch_id(c)),
            }
            for c in courses
        ]
        return json.dumps({"found": True, "count": len(results), "courses": results})
    except Exception as e:
        logger.error(f"[course_progress_tools] get_user_course_enrollments error: {e}")
        return json.dumps({"found": False, "error": str(e)})


# ── STEPS 2-5 (+ R1/R2 revalidation): progress / certificate diagnosis ──────────

@tool
def diagnose_course_progress(course_id: str, email: str, is_revalidation: bool = False) -> str:
    """Run the full progress/certificate diagnosis for one course or program the
    user is enrolled in.

    Always re-fetches the enrollment record fresh, so calling this a second time
    with is_revalidation=true IS the SOP's "revalidation" re-check — the same
    logic, run again to see if anything changed.

    Returns one `status`:
      "certificate_issued"   — certificate already generated. Close, no ticket.
      "not_enrolled"         — no enrollment / no active batch for this course.
                                Close, no ticket.
      "technical_issue"      — sync mismatch between portal and backend found for
                                at least one resource. RAISE ticket (escalate).
      "needs_revalidation"   — looks 100% complete (or no incomplete resources
                                found) but certificate not yet issued and no
                                mismatch detected. Call this tool AGAIN with
                                is_revalidation=true before deciding anything.
      "certificate_generation_failure" — only returned when is_revalidation=true:
                                still complete, still no certificate, still no
                                mismatch on the second check. RAISE ticket.
      "resources_pending"    — genuinely incomplete; see incomplete_ids — pass
                                those to get_incomplete_resource_details next.
      "error"                — something failed; treat as unknown, do not guess.
    """
    try:
        user = _fetch_user_record(email)
        if not user or not user.get("id"):
            return json.dumps({"status": "error", "message": USER_NOT_FOUND_MESSAGE})
        user_id = user["id"]

        courses = _fetch_enrollments(user_id, ["In-Progress", "Completed"])
        course = next((c for c in courses if c.get("courseId") == course_id), None)
        if not course:
            return json.dumps({"status": "not_enrolled", "message": "No enrollment record found for this course/program."})

        course_name = course.get("courseName")
        completion_pct = course.get("completionPercentage")
        batch_id = _extract_batch_id(course)

        if _has_issued_certificate(course):
            return json.dumps({"status": "certificate_issued", "course_name": course_name})

        if not batch_id:
            return json.dumps({"status": "not_enrolled", "course_name": course_name})

        content = _fetch_content(course_id)
        primary_category = content.get("primaryCategory")

        if primary_category in _PROGRAM_CATEGORIES:
            child_course_ids = _fetch_hierarchy_child_course_ids(course_id) or [course_id]
        else:
            child_course_ids = [course_id]

        admin_records: list = []
        for cid in child_course_ids:
            try:
                admin_records.extend(_fetch_admin_content_state(user_id, cid, batch_id))
            except Exception as e:
                logger.warning(f"[course_progress_tools] admin content state failed for {cid}: {e}")

        lang_content_status = course.get("langContentStatus") or {}
        if _detect_technical_issue(lang_content_status, admin_records):
            return json.dumps({
                "status": "technical_issue",
                "course_name": course_name,
                "primary_category": primary_category,
            })

        if completion_pct == 100:
            if is_revalidation:
                return json.dumps({"status": "certificate_generation_failure", "course_name": course_name})
            return json.dumps({"status": "needs_revalidation", "course_name": course_name})

        # STEP 5 — cross-enrollment leaf-node diff: a container course's own
        # enrollment record may never carry a nested child course's completion,
        # so scan the child courses' own enrollment records too, not just this
        # course's. Scoped to this program's own child_course_ids (already
        # fetched above) rather than every enrollment the user has anywhere —
        # an unrelated enrollment reusing the same leaf resource ID would
        # otherwise falsely mark it complete here.
        relevant_course_ids = set(child_course_ids) | {course_id}
        try:
            all_enrollments = _fetch_enrollments(user_id, ["0", "1", "2"])
        except Exception as e:
            logger.warning(f"[course_progress_tools] all-enrollments fetch failed: {e}")
            all_enrollments = []

        completed_ids: set = set()
        for c in all_enrollments:
            if c.get("courseId") not in relevant_course_ids:
                continue
            flat = _flatten_lang_content_status(c.get("langContentStatus") or {})
            completed_ids |= {rid for rid, status in flat.items() if status == 2}

        leaf_nodes = content.get("leafNodes") or []
        incomplete_ids = [rid for rid in leaf_nodes if rid not in completed_ids]

        if not incomplete_ids:
            if is_revalidation:
                return json.dumps({"status": "certificate_generation_failure", "course_name": course_name})
            return json.dumps({"status": "needs_revalidation", "course_name": course_name})

        return json.dumps({
            "status": "resources_pending",
            "course_name": course_name,
            "primary_category": primary_category,
            "incomplete_ids": incomplete_ids,
        })
    except Exception as e:
        logger.error(f"[course_progress_tools] diagnose_course_progress error: {e}")
        return json.dumps({"status": "error", "error": str(e)})


# ── STEP 6 / 6a: pending resource metadata ──────────────────────────────────────

@tool
def get_incomplete_resource_details(incomplete_ids: list[str], email: str) -> str:
    """Fetch name/type/duration for each pending resource and classify which
    guidance branch applies.

    email is unused by the API itself but required by convention (auto-injected
    from session context).

    Returns:
      all_resources_assessment (bool) — every incomplete resource is an assessment
      has_scorm_resources (bool)      — at least one incomplete resource is SCORM
      resource_names (list[str])      — all pending resource names
      scorm_resource_names / non_scorm_resource_names (list[str])
      scorm_resource_name / scorm_resource_duration_min — first SCORM resource's
        name and total duration in minutes (the SCORM guidance message tells the
        user to complete at least 60% of this)
      assessment_id — do_id of the first pending resource, for
        get_assessment_remaining_attempts
      max_attempts / max_retake_attempts — fallback attempt counts
    """
    try:
        found = _fetch_composite_metadata(incomplete_ids)
        found_by_id = {item.get("identifier"): item for item in found if item.get("identifier")}

        for rid in [rid for rid in incomplete_ids if rid not in found_by_id]:
            try:
                content = _fetch_content(rid)
                if content:
                    found_by_id[rid] = content
            except Exception as e:
                logger.warning(f"[course_progress_tools] fallback content read failed for {rid}: {e}")

        items = [found_by_id[rid] for rid in incomplete_ids if rid in found_by_id]
        if not items:
            return json.dumps({"error": "Could not fetch metadata for any pending resource."})

        all_resources_assessment = all(item.get("primaryCategory") == _ASSESSMENT_CATEGORY for item in items)
        scorm_items = [item for item in items if item.get("mimeType") == _SCORM_MIME_TYPE]
        non_scorm_items = [item for item in items if item.get("mimeType") != _SCORM_MIME_TYPE]

        result = {
            "all_resources_assessment": all_resources_assessment,
            "has_scorm_resources": bool(scorm_items),
            "resource_names": [item.get("name") for item in items if item.get("name")],
            "scorm_resource_names": [item.get("name") for item in scorm_items if item.get("name")],
            "non_scorm_resource_names": [item.get("name") for item in non_scorm_items if item.get("name")],
        }

        if scorm_items:
            first_scorm = scorm_items[0]
            result["scorm_resource_name"] = first_scorm.get("name")
            duration = first_scorm.get("duration")
            try:
                result["scorm_resource_duration_min"] = round(float(duration) / 60, 1) if duration else None
            except (TypeError, ValueError):
                result["scorm_resource_duration_min"] = None

        first_item = items[0]
        result["assessment_id"] = first_item.get("identifier")
        result["max_attempts"] = first_item.get("maxAttempts")
        result["max_retake_attempts"] = first_item.get("maxAssessmentRetakeAttempts")

        return json.dumps(result)
    except Exception as e:
        logger.error(f"[course_progress_tools] get_incomplete_resource_details error: {e}")
        return json.dumps({"error": str(e)})


# ── STEP 7: assessment retake count ─────────────────────────────────────────────

@tool
def get_assessment_remaining_attempts(assessment_id: str, email: str) -> str:
    """Fetch the user's remaining assessment attempts. Only call this when the
    user reports an attempt-limit error specifically — not for a plain pending
    assessment.

    Returns remaining_attempts, max_attempts, used_attempts.
      remaining_attempts > 0  -> tell the user the exact count, ask them to retry. NO ticket.
      remaining_attempts == 0 -> RAISE ticket — attempts genuinely exhausted.
    """
    try:
        user = _fetch_user_record(email)
        if not user or not user.get("id"):
            return json.dumps({"error": USER_NOT_FOUND_MESSAGE})
        user_id = user["id"]

        url = f"{IGOT_API_HOST_URL}/api/admin/assesment/retake/count"
        params = {"assessmentIdentifier": assessment_id, "userId": user_id, "editMode": "false"}
        resp = requests.get(url, params=params, headers=_HEADERS_JSON, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        allowed = data.get("attemptsAllowed")
        made = data.get("attemptsMade")
        if allowed is None or made is None:
            return json.dumps({"error": "Assessment attempt data not available."})

        remaining = max(_as_int(allowed) - _as_int(made), 0)
        return json.dumps({
            "remaining_attempts": remaining,
            "max_attempts": allowed,
            "used_attempts": made,
        })
    except Exception as e:
        logger.error(f"[course_progress_tools] get_assessment_remaining_attempts error: {e}")
        return json.dumps({"error": str(e)})


# ── Convenience list for the subgraph ───────────────────────────────────────────

def get_course_progress_tools() -> list:
    """Return all tools for the course_program_progress_issue flow."""
    return [
        get_user_course_enrollments,
        diagnose_course_progress,
        get_incomplete_resource_details,
        get_assessment_remaining_attempts,
    ]
