"""
tools/content_resource_tools.py
---------------------------------
Tools for content_related_issue -> content_resource_not_opening (web-browser
case only; the mobile-app case needs no tool calls — see
CONTENT_RESOURCE_NOT_OPENING_SYSTEM_PROMPT).

Built from the UC-04 Resource / Content Not Opening API Integration Guide.

Endpoints (IGOT_API_HOST_URL = https://portal.uat.karmayogibharat.net in UAT):
  POST /api/private/user/v1/search                     — email -> user_id
  POST /api/course/private/v4/user/enrollment/list/{id} — enrollment list (course
                                                            picker + langContentStatus)
  GET  /api/content/v2/read/{course_id}                 — leafNodes (primary) /
                                                            children (fallback)
  POST /api/composite/v4/search                         — resource name/mimeType
  GET  /api/extended/content/v1/read/{id}               — per-resource fallback read
                                                            (for resources composite
                                                            search drops): name/mimeType
  GET  /api/content/v1/read/{id}                         — YouTube resources only:
                                                            streamingUrl/artifactUrl/
                                                            previewUrl (the extended
                                                            endpoint above does not
                                                            return these two fields)

The resource-name matching / incomplete-resource diffing is computed here,
deterministically, matching the pattern used in course_progress_tools.py.
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


def _fetch_course_hierarchy(course_id: str) -> dict:
    """GET /api/content/v2/read/{course_id} -> result.content."""
    url = f"{IGOT_API_HOST_URL}/api/content/v2/read/{course_id}"
    resp = requests.get(url, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("content", {}) or {}


def _fetch_composite_metadata(identifiers: list) -> list:
    """POST /api/composite/v4/search -> content[] metadata (name, mimeType)."""
    url = f"{IGOT_API_HOST_URL}/api/composite/v4/search"
    payload = {
        "request": {
            "filters": {"identifier": identifiers},
            "isSecureSettingsDisabled": True,
            "fields": ["identifier", "name", "mimeType"],
            "limit": 1000,
        }
    }
    resp = requests.post(url, json=payload, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("content", []) or []


def _fetch_content(content_id: str) -> dict:
    """GET /api/extended/content/v1/read/{content_id} -> result.content.
    Used for the STEP 3b missing-resource fallback (name/mimeType only)."""
    url = f"{IGOT_API_HOST_URL}/api/extended/content/v1/read/{content_id}"
    resp = requests.get(url, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("content", {}) or {}


def _fetch_content_urls(content_id: str) -> dict:
    """GET /api/content/v1/read/{content_id} -> result.content.
    Used only for the STEP 4 YouTube URL check — unlike the extended endpoint
    above, this is the only one that actually returns streamingUrl/previewUrl."""
    url = f"{IGOT_API_HOST_URL}/api/content/v1/read/{content_id}"
    resp = requests.get(url, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("content", {}) or {}


# ── STEP 1: enrolled course picker ──────────────────────────────────────────────

@tool
def get_user_course_enrollments(email: str) -> str:
    """Fetch the user's enrolled courses (In-Progress and Completed) so the
    ticket's course name can be fuzzy-matched against this list.

    Returns found, count, and courses: [{course_id, course_name, completion_pct}].
    Pass the matched course_id into get_course_resources next — never guess it.
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
                "course_id":      c.get("courseId"),
                "course_name":    c.get("courseName"),
                "completion_pct": c.get("completionPercentage"),
            }
            for c in courses
        ]
        return json.dumps({"found": True, "count": len(results), "courses": results})
    except Exception as e:
        logger.error(f"[content_resource_tools] get_user_course_enrollments error: {e}")
        return json.dumps({"found": False, "error": str(e)})


# ── STEPS 2+3+3b: resource picker (hierarchy + metadata + fallback) ─────────────

@tool
def get_course_resources(course_id: str, email: str) -> str:
    """Fetch the resources (leaf nodes) for one course the user is enrolled in,
    resolving each one's name, mimeType, and completion status.

    Returns found, course_name, and resources: [{resource_id, resource_name,
    mime_type, is_complete}]. If the user didn't name the resource, prefer the
    entries with is_complete=false — ask the user to confirm which one they
    mean rather than guessing.
    """
    try:
        user = _fetch_user_record(email)
        if not user or not user.get("id"):
            return json.dumps({"found": False, "message": USER_NOT_FOUND_MESSAGE})
        user_id = user["id"]

        courses = _fetch_enrollments(user_id, ["In-Progress", "Completed"])
        course = next((c for c in courses if c.get("courseId") == course_id), None)
        if not course:
            return json.dumps({"found": False, "message": "No enrollment record found for this course."})
        course_name = course.get("courseName")

        hierarchy = _fetch_course_hierarchy(course_id)
        resource_ids = hierarchy.get("leafNodes") or [
            c.get("identifier") for c in (hierarchy.get("children") or []) if c.get("identifier")
        ]
        if not resource_ids:
            return json.dumps({"found": False, "course_name": course_name, "message": "No resources found for this course."})

        found = _fetch_composite_metadata(resource_ids)
        found_by_id = {item.get("identifier"): item for item in found if item.get("identifier")}

        for rid in [rid for rid in resource_ids if rid not in found_by_id]:
            try:
                content = _fetch_content(rid)
                if content:
                    found_by_id[rid] = content
            except Exception as e:
                logger.warning(f"[content_resource_tools] fallback content read failed for {rid}: {e}")

        completed_ids = {
            rid for rid, s in _flatten_lang_content_status(course.get("langContentStatus") or {}).items() if s == 2
        }

        resources = [
            {
                "resource_id":   rid,
                "resource_name": found_by_id[rid].get("name"),
                "mime_type":     found_by_id[rid].get("mimeType"),
                "is_complete":   rid in completed_ids,
            }
            for rid in resource_ids if rid in found_by_id
        ]
        if not resources:
            return json.dumps({"found": False, "course_name": course_name, "message": "No resources found for this course."})

        return json.dumps({"found": True, "course_name": course_name, "resources": resources})
    except Exception as e:
        logger.error(f"[content_resource_tools] get_course_resources error: {e}")
        return json.dumps({"found": False, "error": str(e)})


# ── STEP 4: YouTube URL check (YouTube resources only) ──────────────────────────

@tool
def check_youtube_resource_urls(resource_id: str, email: str) -> str:
    """For a YouTube-type resource (mime_type == 'text/x-url'), fetch its
    streamingUrl, artifactUrl, and previewUrl and check whether they're all
    present and identical — the signal that the resource is correctly
    configured (vs. a content configuration issue).

    email is unused by the API itself but required by convention (auto-injected
    from session context).

    Returns status: "configured" (all three present and identical) or
    "misconfigured" (any URL missing or the three differ).
    """
    try:
        content = _fetch_content_urls(resource_id)
        streaming_url = content.get("streamingUrl")
        artifact_url = content.get("artifactUrl")
        preview_url = content.get("previewUrl")

        if streaming_url and streaming_url == artifact_url == preview_url:
            return json.dumps({"status": "configured"})
        return json.dumps({"status": "misconfigured"})
    except Exception as e:
        logger.error(f"[content_resource_tools] check_youtube_resource_urls error: {e}")
        return json.dumps({"status": "error", "error": str(e)})


# ── Convenience list for the subgraph ───────────────────────────────────────────

def get_content_resource_tools() -> list:
    """Return all tools for the content_resource_not_opening (web-browser case) flow."""
    return [
        get_user_course_enrollments,
        get_course_resources,
        check_youtube_resource_urls,
    ]
