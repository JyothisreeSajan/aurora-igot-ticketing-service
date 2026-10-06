"""
tools/ca_apar_tool.py
------------------------
Tools used exclusively by the CaAparSubgraph.

Covers SOP workflows from Agent_SOP_CA_APAR_Issues.md:

  SOP-1  get_user_cbp_plan     → STEP 1
         get_user_enrollments  → STEP 2
         get_user_profile      → STEP 3, 5A-2, 7A
         get_mdo_details       → STEP 5A-1
         get_yp_am_details     → STEP 5A-1

  SOP-3  get_assigned_cap_courses   → STEP 1
         get_user_enrollments       → STEP 2, 4 (per child course)
         get_cap_hierarchy          → STEP 3 (also classifies each child's
                                      resource_type — Assessment / SCORM /
                                      Non-SCORM — consumed by STEP 4 and STEP 5B)
         get_user_profile           → STEP 1A
         get_mdo_details            → STEP 1A
         get_yp_am_details          → STEP 1A
         get_assessment_attempt_count → STEP 5B (assessment limit exceeded)
"""

import json
import logging

import requests
from langchain.tools import tool

from app.core.tools.course_tools import get_access_settings
from app.core.tools.login_issue_tool import get_yp_am_details
from app.core.tools.profile_update_tool import get_user_profile
from app.core.utils.config import IGOT_API_HOST_URL, IGOT_KEY
from app.core.utils.mdo_lookup import find_mdo_contact

logger = logging.getLogger(__name__)

CONTENT_TYPE_JSON = "application/json"
USER_EMAIL_PLACEHOLDER = "{{USER_EMAIL}}"
USER_PROFILE_NOT_FOUND_MESSAGE = "User profile not found."


def _search_user_by_email(url: str, headers: dict, email: str, timeout: int = 10) -> list[dict]:
    """POST to the User Search API filtered by email; returns the raw content list."""
    payload = {"request": {"filters": {"email": email}}}
    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("response", {}).get("content", [])


def _profile_not_found_response(email: str) -> str:
    return json.dumps({
        "email": USER_EMAIL_PLACEHOLDER,
        "found": False,
        "message": USER_PROFILE_NOT_FOUND_MESSAGE,
        "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email},
    })


def _require_user_id(profile_content: list[dict], email: str, warn_prefix: str | None = None) -> tuple[str | None, str | None]:
    """Validate profile_content from _search_user_by_email and extract user_id.

    Returns (user_id, None) on success, or (None, error_json_string) using the
    standard not-found / id-missing shape shared by get_user_cbp_plan,
    get_assigned_cap_courses, and get_user_cap_assignment.
    """
    if not profile_content:
        if warn_prefix:
            logger.warning(f"[{warn_prefix}] No iGOT profile found for this email.")
        return None, _profile_not_found_response(email)

    user_id = profile_content[0].get("id")
    if not user_id:
        if warn_prefix:
            logger.warning(f"[{warn_prefix}] Profile found but missing id field.")
        return None, json.dumps({
            "email": USER_EMAIL_PLACEHOLDER,
            "found": False,
            "message": "User id not available in profile.",
            "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email},
        })
    return user_id, None


def _fetch_error_response(email: str, error: Exception) -> str:
    return json.dumps({
        "email": USER_EMAIL_PLACEHOLDER,
        "found": False,
        "error": str(error),
        "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email},
    })


def _search_org_by_id(org_ids: list[str], limit: int, timeout: int = 10) -> list[dict]:
    """POST to the Org Search API filtered by id; returns the raw content list."""
    url = f"{IGOT_API_HOST_URL}/api/org/v1/search"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}
    payload = {"request": {"filters": {"id": org_ids}, "limit": limit}}
    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("response", {}).get("content", [])


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


@tool
def get_user_enrollments(email: str, status_filter: str | None = None, content_id: str | None = None) -> str:
    """Fetch progress/completion status for the user's enrolled courses.

    Used in SOP-1 STEP 2 to report course progress alongside the CBP plan.
    status_filter can be 'In-Progress' or 'Completed'. If not passed, fetches both.

    content_id: pass this when checking one specific course (e.g. Edge Case 1)
    so the lookup is not limited to the 20 most recently enrolled courses —
    without it, a course enrolled further in the past could be missed entirely
    and wrongly read as "never started".
    """
    try:
        search_url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
        headers = {
            "Authorization": f"Bearer {IGOT_KEY}",
            "Content-Type": CONTENT_TYPE_JSON,
        }
        content = _search_user_by_email(search_url, headers, email)

        if not content:
            return json.dumps({"error": "User not found.", "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email}})
        user_id = content[0].get("id")
        if not user_id:
            return json.dumps({"error": "User found but user_id is empty.", "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email}})

        api_status = ["In-Progress", "Completed"]
        if status_filter == "In-Progress":
            api_status = ["In-Progress"]
        elif status_filter == "Completed":
            api_status = ["Completed"]

        enroll_url = f"{IGOT_API_HOST_URL}/api/course/private/v4/user/enrollment/list/{user_id}"
        enroll_resp = requests.post(enroll_url, headers=headers, json={"request": {"status": api_status}}, timeout=10)
        enroll_resp.raise_for_status()
        courses = enroll_resp.json().get("result", {}).get("courses", [])

        if content_id:
            match = next((c for c in courses if c.get("contentId") == content_id), None)
            # Deliberately minimal — only what SOP-1 Edge Case 1 and SOP-3 STEP 2/4
            # branch on: whether this specific course is completed, and whether its
            # certificate has actually been issued (a course can be 100% complete
            # with certificateIssued still null/empty). A course with no enrollment
            # record at all and a course that's in-progress both count as "not
            # completed" — the SOP guides the user to the same plan view either way,
            # it does not describe them differently. Extra fields (completionPercentage,
            # enrolledDate, etc.) invite the response to describe details the SOP
            # never asked for.
            return json.dumps({
                "email": USER_EMAIL_PLACEHOLDER,
                "content_id": content_id,
                "course_name": match.get("courseName") if match else None,
                "completed": bool(match and match.get("status") == 2),
                "certificate_issued": bool(
                    match and match.get("status") == 2 and match.get("issuedCertificates")
                ),
                "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email},
            }, indent=2)

        # enrolledDate can be present but None — `or 0` handles that; `.get(key, 0)` alone does not.
        sorted_courses = sorted(courses, key=lambda c: c.get("enrolledDate") or 0, reverse=True)
        filtered_courses = [
            {key: c[key] for key in ENROLLMENT_FIELDS if key in c}
            for c in sorted_courses[:20]
        ]

        return json.dumps({
            "email": USER_EMAIL_PLACEHOLDER,
            "total_fetched": len(courses),
            "returned": len(filtered_courses),
            "courses": filtered_courses,
            "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email},
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Error fetching enrollments: {e!s}", "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email}})


def _flatten_plan(p: dict) -> dict:
    """Extract slim, LLM-friendly fields from a raw CBP plan entry."""
    content_list = p.get("contentList") or []
    course = content_list[0] if content_list else {}
    return {
        "plan_id":    p.get("id"),
        "is_apar":    p.get("isApar"),
        "end_date":   p.get("endDate"),
        "course_name": course.get("name"),
        "content_id":  course.get("identifier"),
    }


@tool
def get_user_cbp_plan(email: str) -> str:
    """Fetch the user's CBP (training) plan — assigned courses with isApar/endDate.

    Used in SOP-1 STEP 1 to determine whether a training plan is assigned at all,
    and in Edge Case 1/2 to check for a specific course.

    Args:
        email: The user's email address.
    """
    search_url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": CONTENT_TYPE_JSON,
    }
    try:
        profile_content = _search_user_by_email(search_url, headers, email)
        user_id, error = _require_user_id(profile_content, email)
        if error:
            return error

        cbp_url = f"{IGOT_API_HOST_URL}/api/supportportal/cbplan/v2/admin/user/list/{user_id}"
        cbp_headers = {
            "Authorization": f"Bearer {IGOT_KEY}",
            "Content-Type": CONTENT_TYPE_JSON,
            "x-authenticated-user-orgid": "igot",
        }
        cbp_resp = requests.get(cbp_url, headers=cbp_headers, timeout=15)
        cbp_resp.raise_for_status()
        cbp_data = cbp_resp.json()

        raw_plans = cbp_data.get("result", {}).get("content", [])
        plans = [_flatten_plan(p) for p in raw_plans]
        apar_plans = [p for p in plans if p.get("is_apar")]
        non_apar_plans = [p for p in plans if not p.get("is_apar")]

        return json.dumps({
            "email":            USER_EMAIL_PLACEHOLDER,
            "first_name":       profile_content[0].get("firstName"),
            "found":            True,
            "total_count":      len(plans),
            "apar_count":       len(apar_plans),
            "non_apar_count":   len(non_apar_plans),
            "apar_plans":       apar_plans,
            "non_apar_plans":   non_apar_plans,
            "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email},
        }, indent=2)

    except Exception as e:
        return _fetch_error_response(email, e)


@tool
def get_assigned_cap_courses(email: str) -> str:
    """Fetch the Comprehensive Assessment Program (CAP) course(s) assigned to a user.

    Used in SOP-3 STEP 1 to verify CAP assignment. Unlike get_user_cbp_plan (which
    returns the user's full CBP plan and relies on an isApar flag), this calls the
    admin "assigned courses" API filtered specifically to the CAP category, so it
    only ever returns actual CAPs.

    Args:
        email: The user's email address.
    """
    search_url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": CONTENT_TYPE_JSON,
    }
    try:
        profile_content = _search_user_by_email(search_url, headers, email)
        user_id, error = _require_user_id(profile_content, email, warn_prefix="get_assigned_cap_courses")
        if error:
            return error

        assigned_url = f"{IGOT_API_HOST_URL}/api/supportportal/admin/user/v2/assignedcourses/{user_id}"
        assigned_headers = {**headers, "x-authenticated-user-token": ""}
        try:
            assigned_resp = requests.post(
                assigned_url,
                headers=assigned_headers,
                json={"courseCategory": "Comprehensive Assessment Program"},
                timeout=15,
            )
            assigned_resp.raise_for_status()
        except requests.exceptions.HTTPError as http_err:
            logger.error(
                "[get_assigned_cap_courses] assignedcourses API returned %s for user_id=%s: %s",
                http_err.response.status_code if http_err.response is not None else "?",
                user_id,
                http_err.response.text if http_err.response is not None else http_err,
            )
            raise
        raw_caps = assigned_resp.json().get("result", {}).get("content", [])

        caps = [
            {
                "plan_id":     c.get("identifier"),
                "course_name": c.get("name"),
                "end_date":    c.get("endDate"),
            }
            for c in raw_caps
            if c.get("identifier")
        ]

        return json.dumps({
            "email":      USER_EMAIL_PLACEHOLDER,
            "first_name": profile_content[0].get("firstName"),
            "found":      True,
            "cap_count":  len(caps),
            "caps":       caps,
            "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email},
        }, indent=2)

    except Exception as e:
        logger.error("[get_assigned_cap_courses] Failed: %s", e)
        return _fetch_error_response(email, e)



SCORM_MIME_TYPE = "application/vnd.ekstep.html-archive"


def _classify_child_resource(primary_category: str | None, mime_type: str | None) -> str:
    """Classify a CAP child node per SOP-3 STEP 3/4.

    "Course Assessment" children are the CAP's own Final Assessment, not a
    completion prerequisite — STEP 4 must skip them, and STEP 5B must target
    their identifier instead of guessing the CAP's own DO_ID doubles as the
    assessment.
    """
    if primary_category == "Course Assessment":
        return "Assessment"
    return "SCORM" if mime_type == SCORM_MIME_TYPE else "Non-SCORM"


@tool
def get_cap_hierarchy(cap_id: str) -> str:
    """Fetch the child courses of a Comprehensive Assessment Program (CAP).

    Used in SOP-3 STEP 3 to determine which child courses must be completed
    (and certified) before the CAP's Final Assessment unlocks. Each child is
    also tagged with a derived `resource_type`:
      - "Assessment"  → this child IS the CAP's Final Assessment (primary_category
        "Course Assessment") — STEP 4 must exclude it from the completion check,
        and STEP 5B must use its identifier for get_assessment_attempt_count.
      - "SCORM"       → a prerequisite course resource packaged as SCORM
        (mime_type "application/vnd.ekstep.html-archive").
      - "Non-SCORM"   → any other prerequisite course resource.

    Args:
        cap_id: The CAP's DO_ID (the plan_id/content_id from get_user_cbp_plan).
    """
    url = f"{IGOT_API_HOST_URL}/api/private/content/v3/hierarchy/{cap_id}"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": CONTENT_TYPE_JSON,
    }
    try:
        response = requests.get(url, headers=headers, timeout=8)
        response.raise_for_status()
        data = response.json()
        content = data.get("result", {}).get("content", {}) or {}
        raw_children = content.get("children") or []

        children = [
            {
                "identifier": c.get("identifier"),
                "name": c.get("name"),
                "primary_category": c.get("primaryCategory"),
                "mime_type": c.get("mimeType"),
                "resource_type": _classify_child_resource(c.get("primaryCategory"), c.get("mimeType")),
            }
            for c in raw_children
            if c.get("identifier")
        ]
        assessment_child = next((c for c in children if c["resource_type"] == "Assessment"), None)

        return json.dumps({
            "cap_id": cap_id,
            "cap_name": content.get("name"),
            "found": bool(children),
            "child_count": len(children),
            "children": children,
            "assessment_child_id": assessment_child["identifier"] if assessment_child else None,
        }, indent=2)
    except Exception as e:
        return json.dumps({
            "cap_id": cap_id,
            "found": False,
            "children": [],
            "assessment_child_id": None,
            "error": f"Error fetching CAP hierarchy: {e!s}",
        })


@tool
def get_assessment_attempt_count(email: str, assessment_identifier: str) -> str:
    """Fetch the number of attempts made and allowed for a specific assessment item.

    Used in SOP-3 STEP 5B to determine whether the CAP Final Assessment's attempt
    limit has actually been exceeded, or how many attempts the user has left.

    Args:
        email: The user's email address (resolved internally to the platform user id).
        assessment_identifier: The assessment's DO_ID. Use `assessment_child_id`
            from get_cap_hierarchy (STEP 3) when it is present — that is the CAP's
            actual "Course Assessment" child. Fall back to the CAP's own DO_ID
            (the plan_id/content_id from STEP 1) only when get_cap_hierarchy found
            no such child, e.g. an older/flat CAP structure.
    """
    search_url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": CONTENT_TYPE_JSON,
    }
    try:
        profile_content = _search_user_by_email(search_url, headers, email)
        if not profile_content:
            return json.dumps({"found": False, "error": "User not found."})
        user_id = profile_content[0].get("id")
        if not user_id:
            return json.dumps({"found": False, "error": "User found but user_id is empty."})

        retake_url = f"{IGOT_API_HOST_URL}/api/admin/assesment/retake/count"
        retake_headers = {"Authorization": f"Bearer {IGOT_KEY}"}
        params = {
            "assessmentIdentifier": assessment_identifier,
            "userId": user_id,
            "editMode": "false",
        }
        retake_resp = requests.get(retake_url, headers=retake_headers, params=params, timeout=8)
        retake_resp.raise_for_status()
        result = retake_resp.json().get("result", {}) or {}

        attempts_made    = result.get("attemptsMade")
        attempts_allowed = result.get("attemptsAllowed")

        if attempts_made is None or attempts_allowed is None:
            return json.dumps({
                "found": False,
                "error": "Attempt count fields missing from API response.",
            })

        remaining = attempts_allowed - attempts_made
        return json.dumps({
            "found":              True,
            "attempts_made":      attempts_made,
            "attempts_allowed":   attempts_allowed,
            "remaining_attempts": remaining,
            "limit_exceeded":     remaining <= 0,
        })
    except Exception as e:
        return json.dumps({"found": False, "error": f"Error fetching assessment attempt count: {e!s}"})


# ── SOP-2 CAP Eligibility check ─────────────────────────────────────────────
# Used together with get_access_settings (reused from course_tools.py) when a
# user reports being unable to ENROLL in a specific CAP they've named — that
# tool's eligibility criteria give organisations as raw rootOrgId values, not
# names, so this resolves them to real org names for the response.

@tool
def resolve_org_names(org_ids: list[str]) -> str:
    """Resolve a list of organisation ids (rootOrgId) to their real org names.

    Used in SOP-2 (Unable to Enroll / eligibility check) to turn the raw
    rootOrgId values returned by get_access_settings' userGroupCriteriaList
    into human-readable organisation names for the response. Designation
    criteria need no resolution — those are already plain names.
    """
    try:
        content = _search_org_by_id(org_ids, len(org_ids) or 1)
        return json.dumps({
            "org_ids": org_ids,
            "resolved": [{"id": o.get("id"), "orgName": o.get("orgName")} for o in content],
        })
    except Exception as e:
        logger.error(f"[ca_apar_tool] resolve_org_names error: {e}")
        return json.dumps({"org_ids": org_ids, "resolved": [], "error": str(e)})


# ── SOP-1 STEP 3 — State Government O.M. exemption check ───────────────────
# The DoPT O.M. mandating APAR Training Plan creation does not apply to State
# Government officials. Confirmed via live lookup: an org's own record (same
# /api/org/v1/search-by-id call as resolve_org_names) reliably reports
# sbOrgType as "ministry" or "state" — unlike ministryOrStateType, which is
# inconsistent junk ("SPV" on both real ministries and real states).

@tool
def get_org_type(root_org_id: str) -> str:
    """Determine whether an organisation is a State Government entity or a
    Central Ministry, via its own org record.

    Used in SOP-1 STEP 3 (no CBP plan exists) to check whether the DoPT O.M.
    mandating APAR Training Plan creation even applies to this user — it does
    NOT apply to State Government officials, so "no plan" is expected/correct
    for them, not something to route toward MDO/Transfer-Request guidance.

    Returns org_type: "state", "ministry", or "unknown" (neither flag set).
    """
    try:
        content = _search_org_by_id([root_org_id], 1)
        if not content:
            return json.dumps({"root_org_id": root_org_id, "found": False, "org_type": "unknown"})

        org = content[0]
        if org.get("isState"):
            org_type = "state"
        elif org.get("isMinistry"):
            org_type = "ministry"
        else:
            org_type = org.get("sbOrgType") or "unknown"

        return json.dumps({
            "root_org_id": root_org_id,
            "found": True,
            "orgName": org.get("orgName"),
            "org_type": org_type,
        })
    except Exception as e:
        logger.error(f"[ca_apar_tool] get_org_type error: {e}")
        return json.dumps({"root_org_id": root_org_id, "found": False, "org_type": "unknown", "error": str(e)})


def _flatten_cap(c: dict) -> dict:
    """Extract slim, LLM-friendly fields from a raw CAP assignment entry.

    The link is constructed the same way the reference flow's action_button
    does — {portal_base_url}/app/toc/{identifier}/overview — confirmed to
    return HTTP 200 for a real assignment, not fabricated. None of the raw
    fields (childNodes, leafNodes, batches, images, etc.) are kept, since that
    much detail invites the response to describe things the SOP never asked
    for.
    """
    identifier = c.get("identifier")
    link = f"{IGOT_API_HOST_URL}/app/toc/{identifier}/overview" if identifier else None
    return {
        "cap_id":    identifier,
        "cap_name":  c.get("name"),
        "end_date":  c.get("endDate"),
        "link":      link,
        # Pre-built so the LLM copies this verbatim instead of constructing its
        # own <a> tag — guarantees it's a real clickable hyperlink, not raw text.
        # Display text is the URL itself, not a "click here" phrase.
        "link_html": f'<a href="{link}" target="_blank" rel="noopener noreferrer">{link}</a>' if link else None,
    }

@tool
def get_user_cap_assignment(email: str) -> str:
    """Fetch the user's assigned Comprehensive Assessment Program (CAP), if any.

    Used in SOP-2 STEP 2 to determine whether a CAP is assigned, and to read its
    name, link, and due date.
    """
    search_url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": CONTENT_TYPE_JSON,
    }
    try:
        profile_content = _search_user_by_email(search_url, headers, email)
        user_id, error = _require_user_id(profile_content, email)
        if error:
            return error

        cap_url = f"{IGOT_API_HOST_URL}/api/supportportal/admin/user/v2/assignedcourses/{user_id}"
        cap_headers = {
            "Authorization": f"Bearer {IGOT_KEY}",
            "Content-Type": CONTENT_TYPE_JSON,
            "x-authenticated-user-token": "",
        }
        cap_resp = requests.post(
            cap_url,
            headers=cap_headers,
            json={"courseCategory": "Comprehensive Assessment Program"},
            timeout=15,
        )
        cap_resp.raise_for_status()
        cap_data = cap_resp.json()

        raw_assignments = cap_data.get("result", {}).get("content") or []
        if isinstance(raw_assignments, dict):
            raw_assignments = [raw_assignments]
        assignments = [_flatten_cap(c) for c in raw_assignments]

        return json.dumps({
            "email": USER_EMAIL_PLACEHOLDER,
            "found": True,
            "total_count": len(assignments),
            "assignments": assignments,
            "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email},
        }, indent=2, default=str)

    except Exception as e:
        return _fetch_error_response(email, e)

@tool
def get_mdo_details(email: str) -> str:
    """Fetch MDO (Mission Director Officer / Org Admin) contact details for a user's organisation.

    Performs two API calls:
      1. Fetch the user profile by email to obtain the rootOrgId.
      2. Search for an active MDO_LEADER in that organisation, falling back to
         MDO_ADMIN only if no MDO_LEADER exists (see mdo_lookup.py).

    Returns MDO admin name, email, mobile, org name, and ministry/state information.

    Used in SOP-1 STEP 5A-1, SOP-3 STEP 1A.
    """
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}

    try:
        profile_content = _search_user_by_email(url, headers, email)
        if not profile_content:
            return _profile_not_found_response(email)

        root_org_id = profile_content[0].get("rootOrgId")
        if not root_org_id:
            return json.dumps({
                "email": USER_EMAIL_PLACEHOLDER,
                "found": False,
                "message": "rootOrgId not available in user profile.",
                "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email},
            })

        admin, _matched_role, match_count = find_mdo_contact(url, headers, root_org_id, timeout=10)
        if admin is None:
            return json.dumps({
                "root_org_id": root_org_id,
                "found": False,
                "message": f"No active MDO Admin found for organisation '{root_org_id}'.",
            })

        pd = admin.get("profileDetails", {})
        personal = pd.get("personalDetails", {})

        real_name = personal.get("firstname", "MDO Admin")
        real_email = personal.get("primaryEmail", "")
        real_mobile = str(personal.get("mobile", ""))

        spoc_replacements = {}
        if real_name:
            spoc_replacements["{{MDO_ADMIN_NAME}}"] = real_name
        if real_email:
            spoc_replacements["{{MDO_ADMIN_EMAIL}}"] = real_email
        if real_mobile:
            spoc_replacements["{{MDO_ADMIN_MOBILE}}"] = real_mobile

        admins = [
            {
                "rootOrgName":            admin.get("rootOrgName", ""),
                "rootOrgId":              admin.get("rootOrgId", ""),
                "mdo_admin_name":         "{{MDO_ADMIN_NAME}}",
                "mdo_admin_email":        "{{MDO_ADMIN_EMAIL}}",
                "mdo_admin_mobile":       "{{MDO_ADMIN_MOBILE}}",
                "ministryOrStateOrgName": pd.get("ministryOrStateOrgName", ""),
                "ministryOrStateType":    pd.get("ministryOrStateType", ""),
                "profileStatus":          pd.get("profileStatus", ""),
            }
        ]

        return json.dumps({
            "root_org_id": root_org_id,
            "found":       True,
            "count":       match_count,
            "admins":      admins,
            "_spoc_replacements": spoc_replacements,
        })

    except Exception as e:
        return _fetch_error_response(email, e)


def get_ca_apar_tools() -> list:
    """Return all tools for the CaAparSubgraph."""
    return [
        get_user_cbp_plan,
        get_assigned_cap_courses,
        get_user_enrollments,
        get_user_cap_assignment,
        get_user_profile,
        get_mdo_details,
        get_yp_am_details,
        get_cap_hierarchy,
        get_assessment_attempt_count,
        get_access_settings,
        resolve_org_names,
        get_org_type,
    ]
