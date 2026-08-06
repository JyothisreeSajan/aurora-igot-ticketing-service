"""
tools/course_tools.py
----------------------
Tools used exclusively by the CoursesSubgraph.

Covers all five SOP workflows from Agent_SOP_Course_Issues.md:

  SOP-C1  composite_content_search  → STEP 3, 8
          get_access_settings        → STEP 5, 6, 7, 9
          get_user_profile           → STEP 5, 6, 7, 9, C1-C4
          get_mdo_details            → STEP 6B, 7, A3, A4, C2-not-assigned
          get_yp_am_details          → STEP 6B (fallback), C2-no-MDO

  SOP-C2  get_user_enrollments       → STEP 1, C5
          get_content_metadata       → STEP 3

  SOP-C3  get_user_enrollments       → STEP 3
          get_content_metadata       → STEP 3

  SOP-C4  get_user_feed              → STEP A1
          get_user_profile           → STEP C1
          get_mdo_details            → STEP A3, A4
          get_apar_assignments       → STEP C5
          get_user_enrollments       → STEP C5

  SOP-C5  (no tools required)

"""

import json
import logging

import requests
from langchain.tools import tool

from app.core.utils.config import IGOT_API_HOST_URL, IGOT_KEY
from app.core.utils.helpers import lookup_yp_by_mdo

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


# ── SOP-C1, C2, C3, C4-C ──────────────────────────────────────────────────────

@tool
def get_user_enrollments(email: str, status_filter: str | None = None) -> str:
    """Fetch all courses/programs/events a user is enrolled in.

    Used in SOP-C1 (enrollment check), SOP-C2 STEP 1 (progress diagnosis),
    SOP-C3 STEP 3 (web browser resource issue), SOP-C4-C STEP C5 (APAR check).

    status_filter can be 'In-Progress' or 'Completed'. If not passed, fetches both.
    """
    try:
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
        logger.error(f"[course_tools] Error fetching enrollments: {e}")
        return json.dumps({"error": f"Error fetching enrollments: {e!s}", "_spoc_replacements": {"{{USER_EMAIL}}": email}})


@tool
def get_user_profile(email: str) -> str:
    """Fetch user profile attributes used for eligibility comparison.

    Used in SOP-C1 STEP 5/6/7/9 (eligibility check against access settings)
    and SOP-C4 SECTION C STEP C1 (SPARROW/APAR — profile verification check).

    Returns: organization, designation, group, profile_verification_status,
    ministry/state, org_id, and other profile attributes.
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

        prof_details = {}
        prof_list = user.get("profileDetails", {}).get("professionalDetails")
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
                "professionalDetails": [
                    {
                        "designation": prof_details.get("designation"),
                        "group": prof_details.get("group"),
                        "verifiedKarmayogi": prof_details.get("verifiedKarmayogi"),
                        "profileStatus": prof_details.get("profileStatus"),
                    }
                ]
            },
            "roles": user.get("roles", [])
        }

        return f"User Profile:\n{json.dumps(profile, indent=2)}"
    except Exception as e:
        return json.dumps({"error": f"Error fetching user profile: {e!s}", "_spoc_replacements": {"{{USER_EMAIL}}": email}})


# ── SOP-C1 — Course/Event search and eligibility ──────────────────────────────

@tool
def composite_content_search(query: str, type: str = "course_or_program", threshold: float = 0.90) -> str:
    """Search for courses, programs, or events by name using the composite search API.

    Used in SOP-C1 STEP 3 (course/program search) and STEP 8 (event search).

    Args:
        query: The course, program, or event name to search for.
        type: One of 'course_or_program' or 'event'.
        threshold: Not used for API search, kept for compatibility.

    Returns a list of matching content items with content_id, name, status, and link.
    """
    url = f"{IGOT_API_HOST_URL}/api/composite/v4/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": "application/json",
    }
    
    primary_category = ["Course", "Program"] if type == "course_or_program" else ["Event"]
    
    payload = {
        "request": {
            "query": query,
            "filters": {
                "primaryCategory": primary_category,
                "status": ["Live", "Review", "Draft", "Retired"]
            },
            "fields": [
                "identifier",
                "name",
                "status",
                "source",
                "organisation",
                "createdOn"
            ],
            "sort_by": {
                "createdOn": "desc"
            },
            "limit": 10,
            "offset": 0
        }
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        results = []
        content_items = data.get("result", {}).get("content", [])
        for item in content_items:
            provider = item.get("source")
            if not provider:
                org = item.get("organisation", [])
                if org and isinstance(org, list):
                    provider = org[0]
                elif isinstance(org, str):
                    provider = org
            if not provider:
                provider = "iGOT Karmayogi"

            results.append({
                "content_id": item.get("identifier"),
                "name": item.get("name"),
                "status": item.get("status"),
                "provider": provider,
                "link": f"https://igot.gov.in/content/{item.get('identifier')}"
            })
            
        return json.dumps({
            "query": query,
            "type": type,
            "total_found": data.get("result", {}).get("count", len(results)),
            "results": results
        })
    except Exception as e:
        return f"Error searching for {type} '{query}': {e!s}"


@tool
def get_access_settings(content_id: str) -> str:
    """Fetch access/eligibility restrictions configured for a course or event.

    Used in SOP-C1 STEP 5, 6, 7, 9 to check if a course or event has eligibility
    restrictions and compare them against the user's profile attributes.

    Returns access criteria such as allowed designations, organizations, or groups
    (via userGroups → userGroupCriteriaList). If no access settings are configured,
    the content is publicly accessible.

    Args:
        content_id: The do_id (e.g. do_1145112679248773121477) of the course/event.
    """
    url = f"{IGOT_API_HOST_URL}/api/accessSettings/read/{content_id}"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Accept": "application/json",
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        result = data.get("result", {})
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error fetching access settings for {content_id}: {e!s}"


@tool
def get_mdo_details(email: str) -> str:
    """Fetch MDO (Mission Director Officer / Org Admin) contact details for a user's organisation.

    Performs two API calls:
      1. Fetch the user profile by email to obtain the rootOrgId.
      2. Search for active MDO_ADMIN users in that organisation.

    Returns MDO admin name, email, mobile, org name, and ministry/state information.

    Used in SOP-C1 STEP 6B/7 (ineligible user → share MDO contact),
    SOP-C4 STEP A3/A4 (eHRMS ID / External System Name missing),
    and SOP-C4 SECTION C (APAR not assigned, no MDO).
    """
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": "application/json",
        "X-Zoho-Ticket-Slug": "TKT",
        "X-Resolution-Agent-Slug": "Resolution-Agent",
    }

    try:
        # ── Step 1: get rootOrgId from user profile ────────────────────────────
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

        # ── Step 2: search for MDO_ADMIN in that org ───────────────────────────
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

        # ── Build filtered result ──────────────────────────────────────────────
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
                "rootOrgName":          admin.get("rootOrgName", ""),
                "rootOrgId":            admin.get("rootOrgId", ""),
                "mdo_admin_name":       "{{MDO_ADMIN_NAME}}",
                "mdo_admin_email":      "{{MDO_ADMIN_EMAIL}}",
                "mdo_admin_mobile":     "{{MDO_ADMIN_MOBILE}}",
                "ministryOrStateOrgName": pd.get("ministryOrStateOrgName", ""),
                "ministryOrStateType":  pd.get("ministryOrStateType", ""),
                "profileStatus":        pd.get("profileStatus", ""),
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

    Used as a fallback in SOP-C1 STEP 6B when MDO details are not available,
    and in SOP-C4 SECTION C when no MDO exists for the user's organization.

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


# ── SOP-C2 / SOP-C3 ───────────────────────────────────────────────────────────

@tool
def get_content_metadata(content_id: str) -> str:
    """Fetch metadata for a course or content item by its identifier (do_id).

    Used in SOP-C2 STEP 3 (identify resource type for progress diagnosis)
    and SOP-C3 STEP 3 (identify resource type when content fails to load on web).

    Returns: contentType, mimeType, name, duration, language, organisation,
    source, batches (enrollment windows), parentCollections, and leafNodes.

    Args:
        content_id: The do_id of the content item (e.g. do_11382688590204108818).
    """
    url = f"{IGOT_API_HOST_URL}/api/content/v1/search"
    headers = {
        "Content-Type": "application/json",
    }
    payload = {
        "request": {
            "filters": {
                "identifier": [content_id]
            },
            "limit": 10
        }
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()

        content_list = data.get("result", {}).get("content", [])
        if not content_list:
            return json.dumps({
                "content_id": content_id,
                "found": False,
                "message": "No content found for the given identifier.",
            })

        item = content_list[0]
        result = {
            "content_id": item.get("identifier"),
            "name": item.get("name"),
            "contentType": item.get("contentType"),
            "mimeType": item.get("mimeType"),
            "mediaType": item.get("mediaType"),
            "duration": item.get("duration"),
            "language": item.get("language"),
            "organisation": item.get("organisation"),
            "source": item.get("source"),
            "batches": item.get("batches", []),
            "parentCollections": item.get("parentCollections", []),
            "leafNodes": item.get("leafNodes", []),
        }
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error fetching content metadata for {content_id}: {e!s}"


# ── SOP-C4 ────────────────────────────────────────────────────────────────────

@tool
def get_user_feed(email: str) -> str:
    """Fetch complete user profile and feed attributes, including eHRMS details and external system mappings.

    Used in SOP-C4 (eHRMS integration) STEP A1 to retrieve the full user profile data,
    eHRMS details, external system mappings, and all attributes returned by the iGOT user search API.
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
            return json.dumps({
                "email": "{{USER_EMAIL}}",
                "error": "User feed not found.",
                "_spoc_replacements": {"{{USER_EMAIL}}": email}
            })
        user_data = content[0]
        
        prof_list = user_data.get("profileDetails", {}).get("professionalDetails", [])
        prof_details = prof_list[0] if isinstance(prof_list, list) and len(prof_list) > 0 else {}
        employment_details = user_data.get("profileDetails", {}).get("employmentDetails", {})
        
        filtered_data = {
            "email": "{{USER_EMAIL}}",
            "id": user_data.get("id"),
            "status": user_data.get("status"),
            "userType": user_data.get("userType"),
            "channel": user_data.get("channel"),
            "rootOrgId": user_data.get("rootOrgId"),
            "rootOrgName": user_data.get("rootOrgName"),
            "externalIds": user_data.get("externalIds", []),
            "profileDetails": {
                "professionalDetails": [
                    {
                        "designation": prof_details.get("designation"),
                        "group": prof_details.get("group"),
                        "profileStatus": prof_details.get("profileStatus"),
                    }
                ],
                "employmentDetails": employment_details
            },
            "_spoc_replacements": {"{{USER_EMAIL}}": email}
        }
        return json.dumps(filtered_data, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Error fetching user feed: {e!s}", "_spoc_replacements": {"{{USER_EMAIL}}": email}})


@tool
def get_apar_assignments(email: str) -> str:
    """Verify APAR / CAP course and assessment assignments for a user.

    Used in SOP-C4 SECTION C STEP C5 to check if APAR-assigned courses
    are present and whether the CAP assessment has been passed.

    Fetches the user's full enrollment list and filters for courses whose
    name contains 'APAR', 'CAP', or 'Annual Performance' (case-insensitive).

    Returns:
        apar_assigned       — True if at least one APAR/CAP course is enrolled.
        cap_assessment_passed — True if any matched course is fully Completed
                               (status=2) AND has at least one issued certificate.
        assignments         — List of matched courses with key completion fields.
        total_enrollments   — Total number of enrollments fetched (for context).
    """
    APAR_KEYWORDS = ["apar", "cap", "annual performance"]

    try:
        # Step 1: resolve email → user_id
        url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
        headers = {
            "Authorization": f"Bearer {IGOT_KEY}",
            "Content-Type": "application/json",
        }
        search_resp = requests.post(
            url,
            json={"request": {"filters": {"email": email}}},
            headers=headers,
            timeout=10,
        )
        search_resp.raise_for_status()
        content = (
            search_resp.json()
            .get("result", {})
            .get("response", {})
            .get("content", [])
        )
        if not content:
            return json.dumps({
                "email": "{{USER_EMAIL}}", 
                "error": "User not found for the given email.",
                "_spoc_replacements": {"{{USER_EMAIL}}": email}
            })

        user_id = content[0].get("id")
        if not user_id:
            return json.dumps({
                "email": "{{USER_EMAIL}}", 
                "error": "User found but user_id is empty.",
                "_spoc_replacements": {"{{USER_EMAIL}}": email}
            })

        # Step 2: fetch all enrollments (both statuses)
        enrollment_data = _get_latest_enrollments(
            user_id=user_id,
            status=["In-Progress", "Completed"],
            top_n=200,  # fetch wide net so we don't miss APAR courses
        )
        all_courses = enrollment_data.get("result", {}).get("courses", [])

        # Step 3: filter for APAR / CAP related courses
        apar_courses = [
            c for c in all_courses
            if any(
                kw in (c.get("courseName") or "").lower()
                for kw in APAR_KEYWORDS
            )
        ]

        # Step 4: derive flags
        apar_assigned = len(apar_courses) > 0
        cap_assessment_passed = any(
            c.get("status") == 2 and bool(c.get("issuedCertificates"))
            for c in apar_courses
        )

        assignments = [
            {
                "courseId": c.get("courseId"),
                "courseName": c.get("courseName"),
                "contentId": c.get("contentId"),
                "status": c.get("status"),
                "completionPercentage": c.get("completionPercentage"),
                "completedOn": c.get("completedOn"),
                "certstatus": c.get("certstatus"),
                "issuedCertificates": c.get("issuedCertificates", []),
                "enrolledDate": c.get("enrolledDate"),
                "active": c.get("active"),
            }
            for c in apar_courses
        ]

        return json.dumps(
            {
                "email": "{{USER_EMAIL}}",
                "user_id": "{{USER_ID}}",
                "apar_assigned": apar_assigned,
                "cap_assessment_passed": cap_assessment_passed,
                "assignments": assignments,
                "total_enrollments_checked": enrollment_data.get("total_fetched", 0),
                "_spoc_replacements": {
                    "{{USER_EMAIL}}": email,
                    "{{USER_ID}}": user_id
                }
            },
            indent=2,
        )
    except Exception as e:
        return f"Error fetching APAR assignments: {e!s}"


# ── Shared — ticket escalation ─────────────────────────────────────────────────



# ── Convenience list for the subgraph ─────────────────────────────────────────

def get_course_tools() -> list:
    """Return all tools for the CoursesSubgraph in SOP execution order."""
    return [
        composite_content_search,  # SOP-C1 STEP 3, 8
        get_access_settings,       # SOP-C1 STEP 5, 6, 7, 9
        get_user_profile,          # SOP-C1 STEP 5, 6, 7, 9 + SOP-C4-C STEP C1
        get_mdo_details,           # SOP-C1 STEP 6B, 7 + SOP-C4 A3, A4, C-no-MDO
        get_yp_am_details,         # SOP-C1 STEP 6B fallback + SOP-C4 C-no-MDO
        get_user_enrollments,      # SOP-C2 STEP 1 + SOP-C3 STEP 3 + SOP-C4-C STEP C5
        get_content_metadata,      # SOP-C2 STEP 3 + SOP-C3 STEP 3
        get_user_feed,             # SOP-C4 SECTION A STEP A1
        get_apar_assignments,      # SOP-C4 SECTION C STEP C5
    ]
