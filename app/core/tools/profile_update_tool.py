"""
tools/profile_update_tool.py
-----------------------------
Tools used exclusively by the ProfileUpdateSubgraph.

Covers all SOP workflows from Agent_SOP_Profile_Update.md:

  SOP-P1  get_user_profile      → STEP 1
          get_org_admin_details → STEP 3
          get_yp_am_details     → STEP 3B, global fallback

  SOP-P2  (no tool calls required — handled by embedded SOP logic)

"""

import json

import requests
from langchain.tools import tool

from app.core.utils.config import IGOT_API_HOST_URL, IGOT_KEY
from app.core.utils.helpers import lookup_yp_by_mdo

# ── SOP-P1 — Profile verification / designation / group ───────────────────────

@tool
def get_user_profile(email: str) -> str:
    """Fetch user profile attributes for eligibility and verification check.

    Used in SOP-P1 STEP 1 (parallel call) to retrieve:
      - verification_status (verified / not_verified)
      - designation
      - group
      - department / organization
      - org_id  (used for get_org_admin_details)
      - ministry_or_state  (used for get_yp_am_details fallback)
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
            return json.dumps({"error": "User profile not found.", "_spoc_replacements": {"{{USER_EMAIL}}": email}})

        user = content[0]

        profile_details = user.get("profileDetails") or {}
        if not isinstance(profile_details, dict):
            profile_details = {}

        prof_details = {}
        prof_list = profile_details.get("professionalDetails")
        if isinstance(prof_list, list) and len(prof_list) > 0:
            prof_details = prof_list[0]

        profile = {
            "id": user.get("id"),
            "firstName": user.get("firstName"),
            "lastName": user.get("lastName"),
            "rootOrgName": user.get("rootOrgName"),
            "rootOrgId": user.get("rootOrgId"),
            "channel": user.get("channel"),
            "userType": user.get("userType"),
            "status": user.get("status"),
            "profileDetails": {
                "profileDesignationStatus": profile_details.get("profileDesignationStatus"),
                "profileStatus": profile_details.get("profileStatus"),
                "professionalDetails": [
                    {
                        "designation": prof_details.get("designation"),
                        "group": prof_details.get("group"),
                        "verifiedKarmayogi": prof_details.get("verifiedKarmayogi"),
                        "profileStatus": prof_details.get("profileStatus"),
                        "department": prof_details.get("department"),
                        "ministry": prof_details.get("ministry"),
                    }
                ]
            },
            "roles": user.get("roles", [])
        }

        return json.dumps({
            "email": "{{USER_EMAIL}}",
            **profile,
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Error fetching user profile: {e!s}", "_spoc_replacements": {"{{USER_EMAIL}}": email}})




@tool
def get_org_admin_details(org_id: str) -> str:
    """Fetch Org Admin / MDO Leader name and email for a given organization (org_id).

    Used in SOP-P1 STEP 3 (designation request pending → check admin availability)
    and as the global fallback whenever an admin is needed.

    Searches for active MDO_ADMIN users in that organisation.
    """
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": "application/json",
        "X-Zoho-Ticket-Slug": "TKT",
        "X-Resolution-Agent-Slug": "Resolution-Agent",
    }

    try:
        admin_payload = {
            "request": {
                "filters": {
                    "rootOrgId": org_id,
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
                "org_id": org_id,
                "found": False,
                "message": f"No active MDO Admin found for organisation '{org_id}'.",
            })

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
                "rootOrgName": admin.get("rootOrgName", ""),
                "rootOrgId": admin.get("rootOrgId", ""),
                "mdo_admin_name": "{{MDO_ADMIN_NAME}}",
                "mdo_admin_email": "{{MDO_ADMIN_EMAIL}}",
                "mdo_admin_mobile": "{{MDO_ADMIN_MOBILE}}",
                "ministryOrStateOrgName": pd.get("ministryOrStateOrgName", ""),
                "ministryOrStateType": pd.get("ministryOrStateType", ""),
                "profileStatus": pd.get("profileStatus", ""),
            }
        ]

        return json.dumps({
            "org_id": org_id,
            "found": True,
            "count": len(admin_content),
            "admins": admins,
            "org_admin_name": "{{MDO_ADMIN_NAME}}",
            "org_admin_email": "{{MDO_ADMIN_EMAIL}}",
            "_spoc_replacements": spoc_replacements,
        })

    except Exception as e:
        return json.dumps({
            "org_id": org_id,
            "found": False,
            "error": str(e),
        })


@tool
def get_yp_am_details(ministry_or_state: str) -> str:
    """Fetch YP (Young Professional) / SPOC contact details for a ministry, state, or MDO.

    Searches the iGOT YP allocation data for the given organisation name.
    Matching is case-insensitive and substring-based — partial names work
    (e.g. 'Gujarat', 'Atomic Energy', 'Department of Defence').

    Used as a fallback in SOP-P1 STEP 3B (no org admin found) and in the global
    fallback rule whenever get_org_admin_details returns no result.

    Returns a JSON string with matched SPOC records or a not-found message.
    """
    try:
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
                "mdo": m0.get("mdo", ""),
                "yp_am_name": "{{YP_AM_NAME}}",
                "yp_am_email": "{{YP_AM_EMAIL}}",
                "mobile": "{{YP_AM_MOBILE}}",
                "yp_email_cc": "{{YP_EMAIL_CC}}",
            }
        ]

        return json.dumps({
            "query": ministry_or_state,
            "found": True,
            "count": len(matches),
            "results": results,
            "yp_am_name": "{{YP_AM_NAME}}",
            "yp_am_email": "{{YP_AM_EMAIL}}",
            "yp_am_mobile": "{{YP_AM_MOBILE}}",
            "yp_email_cc": "{{YP_EMAIL_CC}}",
            "_spoc_replacements": spoc_replacements,
        })
    except Exception as e:
        return json.dumps({
            "query": ministry_or_state,
            "found": False,
            "error": str(e),
        })


# ── Convenience list for the subgraph ─────────────────────────────────────────

def get_profile_update_tools() -> list:
    """Return all tools for the ProfileUpdateSubgraph in SOP execution order."""
    return [
        get_user_profile,               # SOP-P1 STEP 1
        get_org_admin_details,           # SOP-P1 STEP 3
        get_yp_am_details,              # SOP-P1 STEP 3B + global fallback
    ]
