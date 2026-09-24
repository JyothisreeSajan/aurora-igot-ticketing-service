"""
tools/enrolment_tools.py
-------------------------
Tools for content_related_issue -> enrolment_issues: a user unable to find or
enroll in a course, program, moderated course, or event.

Built from the UC-FindCourse API Integration Guide (flows/mode_b_find_course.yaml).
Shared low-level infra (MDO lookup, YP/SPOC CSV lookup, config) is reused from
app.core.utils, matching the pattern every other tools module in this codebase
already follows.

Endpoints (IGOT_API_HOST_URL = https://portal.uat.karmayogibharat.net in UAT):
  GET  /api/user/private/v1/read/{user_id}      — full profile (eligibility context)
  POST /api/private/user/v1/search              — email -> user_id, MDO admin search
  POST /api/composite/v4/search                 — course/program/event search
  GET  /api/accessSettings/read/{content_id}    — access-settings eligibility (Gate 2)

Eligibility is computed here, deterministically, rather than left to the LLM to
reason about raw JSON — matching how the reference flow treats `check_user_eligibility`
and `check_secure_settings_eligibility` as fixed transforms, not free-form judgement.
"""

import json
import logging

import requests
from langchain.tools import tool

from app.core.utils.config import IGOT_API_HOST_URL, IGOT_KEY
from app.core.utils.helpers import lookup_yp_by_mdo
from app.core.utils.mdo_lookup import find_mdo_contact

logger = logging.getLogger(__name__)

_HEADERS_JSON = {
    "Authorization": f"Bearer {IGOT_KEY}",
    "Content-Type": "application/json",
}


# ── Internal helpers ────────────────────────────────────────────────────────────

def _fetch_user_record(email: str) -> dict | None:
    """POST /api/private/user/v1/search by email -> first matching user record."""
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    resp = requests.post(url, json={"request": {"filters": {"email": email}}}, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    content = resp.json().get("result", {}).get("response", {}).get("content", [])
    return content[0] if content else None


def _build_eligibility_ctx(user_id: str) -> dict | None:
    """GET /api/user/private/v1/read/{user_id} and flatten into a criteriaKey-keyed ctx.

    `group`/`designation` are collected across ALL professionalDetails entries (the
    API guide notes these are matched "any entry matches" — a user can hold more
    than one posting/group over time).
    """
    url = f"{IGOT_API_HOST_URL}/api/user/private/v1/read/{user_id}"
    resp = requests.get(url, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    data = resp.json().get("result", {}).get("response", {})
    if not data:
        return None

    prof = data.get("profileDetails", {}) or {}
    prof_list = prof.get("professionalDetails") or []
    groups = [p.get("group") for p in prof_list if p.get("group")]
    designations = [p.get("designation") for p in prof_list if p.get("designation")]
    employment = prof.get("employmentDetails", {}) or {}
    cadre = prof.get("cadreDetails", {}) or {}
    batch = cadre.get("cadreBatch")

    ctx = {
        "group":                groups,
        "designation":          designations,
        "rootOrgId":            data.get("rootOrgId"),
        "user":                 data.get("id"),
        "department":           employment.get("departmentName"),
        "cadre":                cadre.get("cadreName"),
        "service":              cadre.get("civilServiceName"),
        "batch":                str(batch) if batch is not None else None,
        "profile_status":       prof.get("profileStatus"),
        "ministry_or_state_id": prof.get("ministryOrStateId") or data.get("rootOrgId"),
    }
    return {
        "first_name":      data.get("firstName"),
        "root_org_id":     data.get("rootOrgId"),
        "root_org_name":   data.get("rootOrgName"),
        "org_channel":     data.get("channel"),
        "eligibility_ctx": ctx,
    }


def _resolve_eligibility_payload(email: str) -> dict | None:
    """email -> user record -> eligibility ctx, in one call chain."""
    try:
        user = _fetch_user_record(email)
        if not user or not user.get("id"):
            return None
        return _build_eligibility_ctx(user["id"])
    except Exception as e:
        logger.warning(f"[enrolment_tools] _resolve_eligibility_payload failed: {e}")
        return None


def _criteria_group_matches(criteria_list: list, ctx: dict) -> bool:
    """AND across every criterion within one userGroup's criteriaList."""
    for crit in criteria_list or []:
        key = crit.get("criteriaKey") or crit.get("key")
        values = (
            crit.get("criteriaValue")
            or crit.get("criteriaValues")
            or crit.get("values")
            or crit.get("value")
            or []
        )
        if isinstance(values, str):
            values = [values]
        ctx_val = ctx.get(key)
        if ctx_val is None:
            return False
        if isinstance(ctx_val, list):
            if not any(v in values for v in ctx_val):
                return False
        elif ctx_val not in values:
            return False
    return True


def _check_user_eligibility(user_groups: list, ctx: dict) -> bool:
    """OR across userGroups; AND within each group's criteriaList.
    No userGroups configured at all -> publicly accessible -> eligible."""
    if not user_groups:
        return True
    return any(
        _criteria_group_matches(g.get("userGroupCriteriaList") or g.get("criteriaList") or [], ctx)
        for g in user_groups
    )


def _check_secure_settings_eligibility(secure_settings, ctx: dict) -> bool:
    """Gate 1 (moderated-course metadata check). Not a dict -> not moderated -> eligible."""
    if not isinstance(secure_settings, dict):
        return True
    orgs = secure_settings.get("organisation") or []
    if orgs:
        if ctx.get("rootOrgId") not in orgs and ctx.get("ministry_or_state_id") not in orgs:
            return False
    if str(secure_settings.get("isVerifiedKarmayogi", "")).strip().lower() == "yes":
        if ctx.get("profile_status") != "VERIFIED":
            return False
    return True


# ── STEP 1: user eligibility profile ────────────────────────────────────────────

@tool
def get_user_eligibility_profile(email: str) -> str:
    """Fetch the requesting user's profile and eligibility context.

    Used at the start of the flow to get the user's first name (for the greeting)
    and the attributes (group, designation, org, cadre, verification status, etc.)
    that later eligibility checks are compared against. Also returns root_org_name /
    org_channel, used as the fallback lookup key for the YP/SPOC contact tool when
    no MDO admin is found.
    """
    try:
        user = _fetch_user_record(email)
        if not user or not user.get("id"):
            return json.dumps({"found": False, "message": "User profile not found."})
        payload = _build_eligibility_ctx(user["id"])
        if not payload:
            return json.dumps({"found": False, "message": "User profile not found."})
        return json.dumps({"found": True, **payload})
    except Exception as e:
        logger.error(f"[enrolment_tools] get_user_eligibility_profile error: {e}")
        return json.dumps({"found": False, "error": str(e)})


# ── STEP 2/8: search ─────────────────────────────────────────────────────────────

@tool
def search_course_or_program(query: str, email: str) -> str:
    """Search for a course or program by name (all statuses) and evaluate the
    requesting user's eligibility against it.

    No primaryCategory filter is applied — Karmayogi's content taxonomy has more
    course-like categories than "Course"/"Program" alone (e.g. "Curated Program"),
    and filtering on it silently excludes live courses under those categories.

    Only the single top-ranked match is evaluated for eligibility, even when
    multiple results come back — if it's not what the user meant, ask them for
    the exact course name and re-run.

    Returns: found, count, course_id, name, status, is_moderated, link, and
    (when status is LIVE) either 'metadata_eligible' (for moderated courses —
    Gate 1) or nothing further (call check_course_or_event_access next for
    Gate 2 / the regular eligibility check).
    """
    try:
        elig = _resolve_eligibility_payload(email)
        ctx = (elig or {}).get("eligibility_ctx", {})

        url = f"{IGOT_API_HOST_URL}/api/composite/v4/search"
        payload = {
            "request": {
                "query": query,
                "filters": {"status": ["Live", "Review", "Draft", "Retired"]},
                "sort_by": {"createdOn": "desc"},
                "limit": 10,
            }
        }
        resp = requests.post(url, json=payload, headers=_HEADERS_JSON, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("result", {}).get("content", [])
        count = data.get("result", {}).get("count", len(content))

        if not content or not content[0].get("name"):
            return json.dumps({"found": False, "count": 0, "message": "No course/program found for this name."})

        top = content[0]
        secure_settings = top.get("secureSettings")
        is_moderated = isinstance(secure_settings, dict)

        result = {
            "found":        True,
            "count":        count,
            "course_id":    top.get("identifier"),
            "name":         top.get("name"),
            "status":       top.get("status"),
            "is_moderated": is_moderated,
            "link":         f"{IGOT_API_HOST_URL}/app/toc/{top.get('identifier')}/overview",
        }
        if is_moderated:
            result["metadata_eligible"] = _check_secure_settings_eligibility(secure_settings, ctx)

        return json.dumps(result)
    except Exception as e:
        logger.error(f"[enrolment_tools] search_course_or_program error: {e}")
        return json.dumps({"found": False, "error": str(e)})


@tool
def search_event(query: str, email: str) -> str:
    """Search for an event by name (all statuses).

    Returns: found, count, event_id, name, status, link. Events are not moderated
    (no secureSettings) — only the regular access-settings check (Gate 2, via
    check_course_or_event_access) applies.
    """
    try:
        url = f"{IGOT_API_HOST_URL}/api/composite/v4/search"
        payload = {
            "request": {
                "query": query,
                "filters": {
                    "contentType": ["Event"],
                    "status": ["Live", "Review", "Draft", "Retired"],
                },
                "sort_by": {"createdOn": "desc"},
                "limit": 50,
            }
        }
        resp = requests.post(url, json=payload, headers=_HEADERS_JSON, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        events = data.get("result", {}).get("Event", [])
        count = data.get("result", {}).get("count", len(events))

        if not events or not events[0].get("name"):
            return json.dumps({"found": False, "count": 0, "message": "No event found for this name."})

        top = events[0]
        return json.dumps({
            "found":  True,
            "count":  count,
            "event_id": top.get("identifier"),
            "name":     top.get("name"),
            "status":   top.get("status"),
            "link":     f"{IGOT_API_HOST_URL}/app/event-hub/home/{top.get('identifier')}",
        })
    except Exception as e:
        logger.error(f"[enrolment_tools] search_event error: {e}")
        return json.dumps({"found": False, "error": str(e)})


# ── STEP 3/9: access settings eligibility (Gate 2) ──────────────────────────────

@tool
def check_course_or_event_access(content_id: str, email: str) -> str:
    """Check whether the user meets the access-settings eligibility criteria for
    a course, program, or event (regular Step 3 / moderated-course Gate 2 / event
    Step 9 — same endpoint and rule for all three).

    No access configuration on the content -> publicly accessible -> eligible.
    On a genuine API error, no 'eligible' key is returned — treat that as unknown
    and do not assume access either way.
    """
    try:
        user = _fetch_user_record(email)
        user_id = user.get("id") if user else None
        elig = _build_eligibility_ctx(user_id) if user_id else None
        ctx = (elig or {}).get("eligibility_ctx", {})

        url = f"{IGOT_API_HOST_URL}/api/accessSettings/read/{content_id}"
        headers = dict(_HEADERS_JSON)
        if user_id:
            headers["wid"] = user_id

        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 404:
            return json.dumps({"eligible": True, "reason": "No access configuration — publicly accessible."})
        resp.raise_for_status()
        data = resp.json()
        user_groups = ((data.get("result", {}) or {}).get("accessControl", {}) or {}).get("userGroups") or []
        eligible = _check_user_eligibility(user_groups, ctx)
        return json.dumps({"eligible": eligible})
    except Exception as e:
        logger.error(f"[enrolment_tools] check_course_or_event_access error: {e}")
        return json.dumps({"error": str(e)})


# ── STEP 4 / STEP 4b: MDO / YP escalation contact ───────────────────────────────

@tool
def get_mdo_admin(email: str) -> str:
    """Fetch the MDO admin contact for the user's organisation (ineligible-user
    escalation path). Prefers an active MDO_LEADER, falls back to MDO_ADMIN.

    On not-found, also returns 'ministry_or_state_hint' (the user's org name) —
    pass that into get_yp_am_contact as the fallback lookup.
    """
    try:
        user = _fetch_user_record(email)
        if not user or not user.get("id"):
            return json.dumps({"found": False, "message": "User profile not found."})

        root_org_id = user.get("rootOrgId")
        if not root_org_id:
            return json.dumps({"found": False, "message": "rootOrgId not available in user profile."})

        url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
        admin, _matched_role, match_count = find_mdo_contact(url, _HEADERS_JSON, root_org_id, timeout=10)
        if admin is None:
            return json.dumps({
                "found": False,
                "message": f"No active MDO Admin found for organisation '{root_org_id}'.",
                "ministry_or_state_hint": user.get("rootOrgName", ""),
            })

        pd = admin.get("profileDetails", {})
        personal = pd.get("personalDetails", {})
        real_name   = personal.get("firstname", "MDO Admin")
        real_email  = personal.get("primaryEmail", "")
        real_mobile = str(personal.get("mobile", ""))

        spoc_replacements = {}
        if real_name:
            spoc_replacements["{{MDO_ADMIN_NAME}}"] = real_name
        if real_email:
            spoc_replacements["{{MDO_ADMIN_EMAIL}}"] = real_email
        if real_mobile:
            spoc_replacements["{{MDO_ADMIN_MOBILE}}"] = real_mobile

        return json.dumps({
            "found": True,
            "count": match_count,
            "mdo_admin_name":  "{{MDO_ADMIN_NAME}}",
            "mdo_admin_email": "{{MDO_ADMIN_EMAIL}}",
            "_spoc_replacements": spoc_replacements,
        })
    except Exception as e:
        logger.error(f"[enrolment_tools] get_mdo_admin error: {e}")
        return json.dumps({"found": False, "error": str(e)})


@tool
def get_yp_am_contact(ministry_or_state: str) -> str:
    """Fetch YP (Young Professional) / SPOC contact details for a ministry, state,
    or MDO name — fallback when get_mdo_admin returns found=False.

    Matching is case-insensitive and substring-based (e.g. 'Gujarat', 'Atomic Energy').
    """
    try:
        matches = lookup_yp_by_mdo(ministry_or_state)
        if not matches:
            return json.dumps({
                "found": False,
                "message": f"No YP/SPOC record found for '{ministry_or_state}'.",
            })

        m0 = matches[0]
        real_yp_name  = m0.get("spoc", "")
        real_yp_email = m0.get("email", "")
        real_mobile   = str(m0.get("mobile", ""))

        spoc_replacements = {}
        if real_yp_name:
            spoc_replacements["{{YP_AM_NAME}}"] = real_yp_name
        if real_yp_email:
            spoc_replacements["{{YP_AM_EMAIL}}"] = real_yp_email
        if real_mobile:
            spoc_replacements["{{YP_AM_MOBILE}}"] = real_mobile

        return json.dumps({
            "found": True,
            "count": len(matches),
            "yp_am_name":  "{{YP_AM_NAME}}",
            "yp_am_email": "{{YP_AM_EMAIL}}",
            "_spoc_replacements": spoc_replacements,
        })
    except Exception as e:
        logger.error(f"[enrolment_tools] get_yp_am_contact error: {e}")
        return json.dumps({"found": False, "error": str(e)})


# ── Convenience list for the subgraph ───────────────────────────────────────────

def get_enrolment_tools() -> list:
    """Return all tools for the enrolment-issues (unable to enroll/find) flow."""
    return [
        get_user_eligibility_profile,
        search_course_or_program,
        search_event,
        check_course_or_event_access,
        get_mdo_admin,
        get_yp_am_contact,
    ]
