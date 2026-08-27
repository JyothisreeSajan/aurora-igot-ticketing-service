"""
tools/certificate_tools.py
---------------------------
Tools used exclusively by the CertificateSubgraph.

Covers all three SOP workflows from Agent_SOP_Certificate_Issues.md:
  SOP-01  get_user_enrollments  → STEP 2
          get_program_hierarchy → STEP 4   
          get_content_state     → STEP 5   

  SOP-02  get_user_enrollments  → STEP 2

  SOP-03  get_user_details      → STEP 1  (maps to get_user_profile in SOP)
"""

import json
import logging

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
        key=lambda c: c.get("enrolledDate", 0),
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



# ── Convenience list for the subgraph ─────────────────────────────────────────

def get_certificate_tools() -> list:
    """Return all tools for the CertificateSubgraph in SOP execution order."""
    return [
        get_user_enrollments,   # SOP-01 STEP 2, SOP-02 STEP 2
        get_user_details,       # SOP-03 STEP 1
    ]
