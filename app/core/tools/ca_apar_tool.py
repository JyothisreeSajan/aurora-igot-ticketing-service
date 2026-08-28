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
"""

import json

import requests
from langchain.tools import tool

from app.core.tools.login_issue_tool import get_mdo_details, get_yp_am_details
from app.core.tools.profile_update_tool import get_user_profile
from app.core.utils.config import IGOT_API_HOST_URL, IGOT_KEY

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
            "Content-Type": "application/json",
        }
        payload = {"request": {"filters": {"email": email}}}
        search_resp = requests.post(search_url, json=payload, headers=headers, timeout=10)
        search_resp.raise_for_status()
        content = search_resp.json().get("result", {}).get("response", {}).get("content", [])

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

        enroll_url = f"{IGOT_API_HOST_URL}/api/course/private/v4/user/enrollment/list/{user_id}"
        enroll_resp = requests.post(enroll_url, headers=headers, json={"request": {"status": api_status}}, timeout=10)
        enroll_resp.raise_for_status()
        courses = enroll_resp.json().get("result", {}).get("courses", [])

        if content_id:
            match = next((c for c in courses if c.get("contentId") == content_id), None)
            # Deliberately minimal — only what SOP-1 Edge Case 1 branches on: whether
            # this specific course is completed or not. A course with no enrollment
            # record at all and a course that's in-progress both count as "not
            # completed" — the SOP guides the user to the same plan view either way,
            # it does not describe them differently. Extra fields (completionPercentage,
            # certificates, enrolledDate, etc.) invite the response to describe details
            # the SOP never asked for.
            return json.dumps({
                "email": "{{USER_EMAIL}}",
                "content_id": content_id,
                "course_name": match.get("courseName") if match else None,
                "completed": bool(match and match.get("status") == 2),
                "_spoc_replacements": {"{{USER_EMAIL}}": email},
            }, indent=2)

        # enrolledDate can be present but None — `or 0` handles that; `.get(key, 0)` alone does not.
        sorted_courses = sorted(courses, key=lambda c: c.get("enrolledDate") or 0, reverse=True)
        filtered_courses = [
            {key: c[key] for key in ENROLLMENT_FIELDS if key in c}
            for c in sorted_courses[:20]
        ]

        return json.dumps({
            "email": "{{USER_EMAIL}}",
            "total_fetched": len(courses),
            "returned": len(filtered_courses),
            "courses": filtered_courses,
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Error fetching enrollments: {e!s}", "_spoc_replacements": {"{{USER_EMAIL}}": email}})


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
        "Content-Type": "application/json",
    }
    try:
        profile_payload = {"request": {"filters": {"email": email}}}
        profile_resp = requests.post(search_url, json=profile_payload, headers=headers, timeout=10)
        profile_resp.raise_for_status()
        profile_content = profile_resp.json().get("result", {}).get("response", {}).get("content", [])
        if not profile_content:
            return json.dumps({
                "email": "{{USER_EMAIL}}",
                "found": False,
                "message": "User profile not found.",
                "_spoc_replacements": {"{{USER_EMAIL}}": email},
            })

        user_id = profile_content[0].get("id")
        if not user_id:
            return json.dumps({
                "email": "{{USER_EMAIL}}",
                "found": False,
                "message": "User id not available in profile.",
                "_spoc_replacements": {"{{USER_EMAIL}}": email},
            })

        cbp_url = f"{IGOT_API_HOST_URL}/api/supportportal/cbplan/v2/admin/user/list/{user_id}"
        cbp_headers = {
            "Authorization": f"Bearer {IGOT_KEY}",
            "Content-Type": "application/json",
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
            "email":            "{{USER_EMAIL}}",
            "first_name":       profile_content[0].get("firstName"),
            "found":            True,
            "total_count":      len(plans),
            "apar_count":       len(apar_plans),
            "non_apar_count":   len(non_apar_plans),
            "apar_plans":       apar_plans,
            "non_apar_plans":   non_apar_plans,
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "email": "{{USER_EMAIL}}",
            "found": False,
            "error": str(e),
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        })


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
        "Content-Type": "application/json",
    }
    try:
        profile_payload = {"request": {"filters": {"email": email}}}
        profile_resp = requests.post(search_url, json=profile_payload, headers=headers, timeout=10)
        profile_resp.raise_for_status()
        profile_content = profile_resp.json().get("result", {}).get("response", {}).get("content", [])
        if not profile_content:
            return json.dumps({
                "email": "{{USER_EMAIL}}",
                "found": False,
                "message": "User profile not found.",
                "_spoc_replacements": {"{{USER_EMAIL}}": email},
            })

        user_id = profile_content[0].get("id")
        if not user_id:
            return json.dumps({
                "email": "{{USER_EMAIL}}",
                "found": False,
                "message": "User id not available in profile.",
                "_spoc_replacements": {"{{USER_EMAIL}}": email},
            })

        cap_url = f"{IGOT_API_HOST_URL}/api/supportportal/admin/user/v2/assignedcourses/{user_id}"
        cap_headers = {
            "Authorization": f"Bearer {IGOT_KEY}",
            "Content-Type": "application/json",
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
            "email": "{{USER_EMAIL}}",
            "found": True,
            "total_count": len(assignments),
            "assignments": assignments,
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        }, indent=2, default=str)

    except Exception as e:
        return json.dumps({
            "email": "{{USER_EMAIL}}",
            "found": False,
            "error": str(e),
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        })


def get_ca_apar_tools() -> list:
    """Return all tools for the CaAparSubgraph."""
    return [
        get_user_cbp_plan,
        get_user_enrollments,
        get_user_cap_assignment,
        get_user_profile,
        get_mdo_details,
        get_yp_am_details,
    ]
