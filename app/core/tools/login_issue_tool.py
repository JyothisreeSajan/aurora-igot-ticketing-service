"""
tools/login_issue_tool.py
----------------------
Tools used exclusively by the LoginAndRegistrationSubgraph.

Covers all SOP workflows from Agent_SOP_Login_Issues.md:

  SOP-L1  validate_email_domain         → STEP 3
          get_user_profile              → STEP 3A
          get_mdo_details               → STEP 3A, 5A
          get_yp_am_details             → STEP 3A, 5A
          lookup_user_by_contact        → STEP 4, 6
          get_user_enrollments          → STEP 6

  SOP-L2  get_user_profile              → STEP 1
          get_user_transfer_request     → STEP 1
          get_mdo_details               → STEP 2A
          get_yp_am_details             → STEP 2A, 5B
          search_organization           → STEP 5


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

@tool
def get_user_profile(user_id: str) -> str:
    """Fetch user profile attributes used for organization and access verification.
    
    Used in SOP-L1 STEP 3A (get org_id for MDO) and SOP-L2 STEP 1.
    
    Returns: organization, designation, group, profile_verification_status,
    ministry/state, org_id, and other profile attributes.
    """
    url = f"{IGOT_API_HOST_URL}/api/user/private/v1/read/{user_id}"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        result = data.get("result", {}).get("response", {})
        if not result:
            return f"User details for ID {user_id} not found."
            
        prof_details = {}
        prof_list = result.get("profileDetails", {}).get("professionalDetails")
        if isinstance(prof_list, list) and len(prof_list) > 0:
            prof_details = prof_list[0]
            
        filtered_user = {
            "id": result.get("id"),
            "rootOrgId": result.get("rootOrgId"),
            "rootOrgName": result.get("rootOrgName"),
            "channel": result.get("channel"),
            "status": result.get("status"),
            "userType": result.get("userType"),
            "roles": result.get("roles", []),
            "profileDetails": {
                "professionalDetails": [
                    {
                        "designation": prof_details.get("designation"),
                        "group": prof_details.get("group"),
                        "ministryOrStateOrgName": prof_details.get("ministryOrStateOrgName"),
                        "verifiedKarmayogi": prof_details.get("verifiedKarmayogi"),
                    }
                ]
            }
        }
            
        return f"User Profile for ID {user_id}:\n{json.dumps(filtered_user, indent=2)}"
    except Exception as e:
        return f"Error fetching user details for ID {user_id}: {e!s}"

@tool
def validate_email_domain(email: str) -> str:
    """Check if the email domain is whitelisted using official API.
    
    Used in SOP-L1 STEP 3.
    """
    domain = email.split("@")[-1].strip().lower()
    url = f"{IGOT_API_HOST_URL}/api/user/v1/email/approvedDomains"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        domains = data.get("result", {}).get("domains", [])
        whitelisted = [d.strip().lower() for d in domains if isinstance(d, str)]
        
        if domain in whitelisted:
            return f"Domain {domain} is whitelisted."
        return f"Domain {domain} is NOT whitelisted."
    except Exception as e:
        return f"Error validating email domain"

@tool
def lookup_user_by_contact(email: str) -> str:
    """Check whether an email is already registered on the iGOT platform.

    Used in SOP-L1 STEP 4, 6.

    Searches by email using the private user search API.
    Returns account details if found: user_id, name, org, status.
    If not found, returns is_registered: false.

    Args:
        email: The email address to look up.
    """
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "request": {
            "filters": {
                "email": email
            }
        }
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()

        content = data.get("result", {}).get("response", {}).get("content", [])
        if not content:
            return json.dumps({
                "email": "{{USER_EMAIL}}",
                "is_registered": False,
                "user": None,
                "_spoc_replacements": {"{{USER_EMAIL}}": email}
            })

        user = content[0]        
        return json.dumps({
            "email": "{{USER_EMAIL}}",
            "is_registered": True,
            "user": {
                "user_id": user.get("id"),
                "rootOrgId": user.get("rootOrgId"),
                "rootOrgName": user.get("rootOrgName"),
                "channel": user.get("channel"),
                "status": user.get("status"),
                "userType": user.get("userType"),
                "organisations": user.get("organisations",{})
            },
            "_spoc_replacements": {"{{USER_EMAIL}}": email}
        }, indent=2)
    except Exception as e:
        return f"Error looking up user by email: {e!s}"

@tool
def get_user_enrollments(email: str, status_filter: str | None = None) -> str:
    """Fetch all courses/programs a user is enrolled in.

    Used in SOP-L1 STEP 6 to compare existing vs new account enrollments
    and determine which account has more learning history.

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
        return json.dumps({
            "email": "{{USER_EMAIL}}",
            **enrollments,
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        }, indent=2)
    except Exception as e:
        logger.error(f"[login_issue_tool] Error fetching enrollments: {e}")
        return json.dumps({"error": f"Error fetching enrollments: {e!s}", "_spoc_replacements": {"{{USER_EMAIL}}": email}})

@tool
def get_user_transfer_request(user_id: str) -> str:
    """Read the wfTransferRequest field from the User Search API for a given user ID.

    Used in SOP-L2 STEP 1 to check whether a pending department/organisation
    transfer request exists for the user.

    Searches by user ID using the private user search API (same endpoint as
    lookup_user_by_contact) and returns the wfTransferRequest field value.
    Returns an empty dict if no transfer request is present.

    Args:
        user_id: The platform user ID (id field from the user profile).
    """
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": "application/json",
        "X-Zoho-Ticket-Slug": "TKT",
        "X-Resolution-Agent-Slug": "Resolution-Agent",
    }
    payload = {
        "request": {
            "filters": {
                "id": user_id,
            }
        }
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        content = data.get("result", {}).get("response", {}).get("content", [])

        if not content:
            return json.dumps({
                "user_id": user_id,
                "found": False,
                "wfTransferRequest": None,
                "message": f"No user found with id '{user_id}'.",
            })

        user = content[0]
        wf_transfer = user.get("wfTransferRequest")

        return json.dumps({
            "user_id":           user_id,
            "found":             True,
            "wfTransferRequest": wf_transfer,
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "user_id": user_id,
            "found":   False,
            "error":   str(e),
        })


@tool
def get_mdo_details(email: str) -> str:
    """Fetch MDO (Mission Director Officer / Org Admin) contact details for a user's organisation.

    Performs two API calls:
      1. Fetch the user profile by email to obtain the rootOrgId.
      2. Search for active MDO_ADMIN users in that organisation.

    Returns MDO admin name, email, mobile, org name, and ministry/state information.

    Used in SOP-L1 STEP 3A/5A, and SOP-L2 STEP 2A.
    """
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": "application/json",
        "X-Zoho-Ticket-Slug": "TKT",
        "X-Resolution-Agent-Slug": "Resolution-Agent",
    }

    try:
        # ── Step 1: get rootOrgId from user profile ─────────────────────────────
        profile_payload = {"request": {"filters": {"email": email}}}
        profile_resp = requests.post(url, json=profile_payload, headers=headers, timeout=10)
        profile_resp.raise_for_status()
        profile_data = profile_resp.json()

        profile_content = (
            profile_data.get("result", {})
                        .get("response", {})
                        .get("content", [])
        )
        if not profile_content:
            return json.dumps({
                "email": "{{USER_EMAIL}}",
                "found": False,
                "message": "User profile not found.",
                "_spoc_replacements": {"{{USER_EMAIL}}": email},
            })

        root_org_id = profile_content[0].get("rootOrgId")
        if not root_org_id:
            return json.dumps({
                "email": "{{USER_EMAIL}}",
                "found": False,
                "message": "rootOrgId not available in user profile.",
                "_spoc_replacements": {"{{USER_EMAIL}}": email},
            })

        # ── Step 2: search for MDO_ADMIN in that org ─────────────────────────
        admin_payload = {
            "request": {
                "filters": {
                    "rootOrgId": root_org_id,
                    "organisations.roles": ["MDO_ADMIN"],
                    "status": 1,
                }
            }
        }
        admin_resp = requests.post(url, json=admin_payload, headers=headers, timeout=10)
        admin_resp.raise_for_status()
        admin_data = admin_resp.json()

        admin_content = (
            admin_data.get("result", {})
                      .get("response", {})
                      .get("content", [])
        )
        if not admin_content:
            return json.dumps({
                "root_org_id": root_org_id,
                "found": False,
                "message": f"No active MDO Admin found for organisation '{root_org_id}'.",
            })

        # ── Build filtered result ──────────────────────────────────────────
        admin = admin_content[0]
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
            "count":       len(admin_content),
            "admins":      admins,
            "_spoc_replacements": spoc_replacements,
        })

    except Exception as e:
        return json.dumps({
            "email": "{{USER_EMAIL}}",
            "found": False,
            "error": str(e),
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        })

@tool
def get_yp_am_details(ministry_or_state: str) -> str:
    """Fetch YP (Young Professional) / SPOC contact details for a ministry, state, or MDO.

    Searches the iGOT YP allocation data for the given organisation name.
    Matching is case-insensitive and substring-based — partial names work
    (e.g. 'Gujarat', 'Atomic Energy', 'Department of Defence').

    Used as a fallback in SOP-L1 STEP 3A/5A, and SOP-L2 STEP 2A/5B.

    Returns a JSON string with matched SPOC records or a not-found message.
    """
    try:
        from app.core.utils.helpers import lookup_yp_by_mdo
        matches = lookup_yp_by_mdo(ministry_or_state)

        if not matches:
            return json.dumps({
                "query": ministry_or_state,
                "found": False,
                "message": f"No YP/SPOC record found for '{ministry_or_state}'. "
                           "Please contact the iGOT support team directly.",
                "results": [],
            })

        m0 = matches[0]
        real_yp_name = m0.get("spoc", "")
        real_yp_email = m0.get("email", "")
        real_mobile = str(m0.get("mobile", ""))
        real_yp_cc = m0.get("yp_email", "")

        spoc_replacements = {}
        if real_yp_name:
            spoc_replacements["{{YP_AM_NAME}}"] = real_yp_name
        if real_yp_email:
            spoc_replacements["{{YP_AM_EMAIL}}"] = real_yp_email
        if real_mobile:
            spoc_replacements["{{YP_AM_MOBILE}}"] = real_mobile
        if real_yp_cc:
            spoc_replacements["{{YP_EMAIL_CC}}"] = real_yp_cc

        results = [
            {
                "centre_state": m0.get("centre_state", ""),
                "mdo":          m0.get("mdo", ""),
                "yp_am_name":   "{{YP_AM_NAME}}",
                "yp_am_email":  "{{YP_AM_EMAIL}}",
                "mobile":       "{{YP_AM_MOBILE}}",
                "yp_email_cc":  "{{YP_EMAIL_CC}}",
            }
        ]

        return json.dumps({
            "query":   ministry_or_state,
            "found":   True,
            "count":   len(matches),
            "results": results,
            "_spoc_replacements": spoc_replacements,
        })
    except Exception as e:
        return json.dumps({
            "query": ministry_or_state,
            "found": False,
            "error": str(e),
        })


def get_login_tools() -> list:
    """Return all tools for the LoginAndRegistrationSubgraph."""
    return [
        get_user_profile,
        validate_email_domain,
        lookup_user_by_contact,
        get_user_enrollments,
        get_user_transfer_request,
        get_mdo_details,
        get_yp_am_details,
    ]
