"""
tools/profile_user_management_tools.py
-----------------------------------------
Tools used exclusively by the ProfileUserManagementSubgraph.

Covers SOP workflows from Agent_SOP_Profile_User_Management.md:

  SOP-A1  Access Revoked (transfer request already raised)
          get_user_transfer_request_details -> STEP 1 wfTransferRequest check
                                                 + greeting name
          get_mdo_details_by_org_id          -> STEP 2 MDO lookup for the
                                                 target transfer organisation
                                                 (not the user's own org)

  SOP-P3  Profile Update - Designation Not Found
          search_designation                 -> STEP 1 identify the exact
                                                 designation against master data
          get_user_root_org_id                -> STEP 2 the user's own rootOrgId
          get_org_imported_designations        -> STEP 2 has this org's MDO
                                                 imported that designation yet
"""

import json
import logging

import requests
from langchain.tools import tool

from app.core.utils.config import IGOT_API_HOST_URL, IGOT_KEY

logger = logging.getLogger(__name__)


# ── SOP-A1 STEP 1 — wfTransferRequest check ─────────────────────────────────

@tool
def get_user_transfer_request_details(email: str) -> str:
    """Check whether the user has an existing pending organisation transfer
    request, via the User Search API's wfTransferRequest field.

    Used in SOP-A1 STEP 1 to determine whether a transfer request has already
    been raised, and if so, which organisation it targets.

    Field mapping (confirmed via live UAT inspection):
      wfTransferRequest.wfId            -> wf_transfer_id
      wfTransferRequest.departmentName  -> transfer_dept_name
      wfTransferRequest.rootOrgId       -> transfer_root_org_id
      wfTransferRequest.orgId           -> transfer_org_id
      wfTransferRequest.organisationId  -> transfer_organisation_id
      wfTransferRequest.orgName         -> transfer_org_name

    An empty {} wfTransferRequest means no transfer request has been raised.
    """
    try:
        url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
        headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": "application/json"}
        payload = {"request": {"filters": {"email": email}}}
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        content = resp.json().get("result", {}).get("response", {}).get("content", [])

        if not content:
            return json.dumps({"found": False, "message": "User profile not found.",
                                "_spoc_replacements": {"{{USER_EMAIL}}": email}})

        user = content[0]
        wf_transfer = user.get("wfTransferRequest") or {}
        has_transfer_request = bool(wf_transfer)

        return json.dumps({
            "email": "{{USER_EMAIL}}",
            "found": True,
            "firstName": user.get("firstName"),
            "has_transfer_request": has_transfer_request,
            "wf_transfer_id": wf_transfer.get("wfId"),
            "transfer_dept_name": wf_transfer.get("departmentName"),
            "transfer_root_org_id": wf_transfer.get("rootOrgId"),
            "transfer_org_id": wf_transfer.get("orgId"),
            "transfer_organisation_id": wf_transfer.get("organisationId"),
            "transfer_org_name": wf_transfer.get("orgName"),
            "_spoc_replacements": {"{{USER_EMAIL}}": email},
        }, indent=2)
    except Exception as e:
        logger.error(f"[profile_user_management_tools] get_user_transfer_request_details error: {e}")
        return json.dumps({"found": False, "error": str(e),
                            "_spoc_replacements": {"{{USER_EMAIL}}": email}})


# ── SOP-A1 STEP 2 — MDO lookup for a SPECIFIC (target) organisation ────────
# get_mdo_details (login_issue_tool.py) always derives the org from the
# user's OWN profile — unusable here, since the whole point of this flow is
# that the user's own org is the "iGOT" placeholder, not the org their
# transfer request actually targets. This reuses the same MDO_ADMIN search
# logic, parameterized by org_id directly instead of deriving it from email.

@tool
def get_mdo_details_by_org_id(org_id: str) -> str:
    """Fetch MDO Admin contact details for a SPECIFIC organisation id.

    Used in SOP-A1 STEP 2 to find the MDO Admin for the organisation a
    transfer request actually targets (transfer_root_org_id from
    get_user_transfer_request_details) — not the user's own current
    organisation.
    """
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": "application/json",
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
        admin_content = admin_resp.json().get("result", {}).get("response", {}).get("content", [])

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

        return json.dumps({
            "org_id": org_id,
            "found": True,
            "rootOrgName": admin.get("rootOrgName", ""),
            "mdo_admin_name": "{{MDO_ADMIN_NAME}}",
            "mdo_admin_email": "{{MDO_ADMIN_EMAIL}}",
            "mdo_admin_mobile": "{{MDO_ADMIN_MOBILE}}",
            "_spoc_replacements": spoc_replacements,
        })
    except Exception as e:
        logger.error(f"[profile_user_management_tools] get_mdo_details_by_org_id error: {e}")
        return json.dumps({"org_id": org_id, "found": False, "error": str(e)})


# ── SOP-A1 Edge Case 2 — organization search ─────────────────────────────────

@tool
def search_organization(org_name: str) -> str:
    """Search for an organization by its exact name via the Org Search API.

    Used in SOP-A1 Edge Case 2 to verify whether an organization the user
    named (but couldn't find in the Transfer Request dropdown) actually
    exists on the platform.

    Matching is EXACT (case-insensitive) — this API does not support partial/
    substring matching, so the org_name passed in must be the exact name as
    given by the user, not a fragment of it.
    """
    url = f"{IGOT_API_HOST_URL}/api/org/v1/search"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": "application/json"}
    try:
        payload = {"request": {"filters": {"orgName": [org_name]}, "limit": 5}}
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        content = resp.json().get("result", {}).get("response", {}).get("content", [])

        if not content:
            return json.dumps({"query": org_name, "found": False})

        org = content[0]
        return json.dumps({
            "query": org_name,
            "found": True,
            "org_id": org.get("id"),
            "org_name": org.get("orgName") or org.get("channel"),
            "ministry_or_state_type": org.get("ministryOrStateType"),
        })
    except Exception as e:
        logger.error(f"[profile_user_management_tools] search_organization error: {e}")
        return json.dumps({"query": org_name, "found": False, "error": str(e)})


@tool
def search_organization_under_ministry_or_state(ministry_or_state_name: str, org_name: str) -> str:
    """Search for an organization under a named Ministry or State, when an exact
    org-name search (search_organization) already failed.

    Used in SOP-A1 Edge Case 2 as a second attempt, only when the user's ticket
    ALSO names a Ministry or State (not just the organization). Two-step lookup:
      1. Find the Ministry or State by name (partial/case-insensitive match)
         via the Ministry/State hierarchy list APIs.
      2. Search organizations under that Ministry/State (also partial/
         case-insensitive match on org_name) via the Org Hierarchy Search API.
    """
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": "application/json"}
    base = IGOT_API_HOST_URL
    try:
        parent_id = None
        parent_label = None
        for path in ("/api/org/hierarchy/ministry/search", "/api/org/hierarchy/state/search"):
            resp = requests.post(f"{base}{path}", json={"request": {}}, headers=headers, timeout=10)
            resp.raise_for_status()
            content = resp.json().get("result", {}).get("response", {}).get("content", [])
            match = next(
                (c for c in content if ministry_or_state_name.lower() in (c.get("channel") or "").lower()),
                None,
            )
            if match:
                parent_id = match.get("id")
                parent_label = match.get("channel")
                break

        if not parent_id:
            return json.dumps({
                "ministry_or_state_query": ministry_or_state_name,
                "parent_found": False,
                "found": False,
            })

        hierarchy_payload = {
            "request": {
                "filters": {"status": 1, "levelZeroOrgId": parent_id},
                "query": "",
                "limit": 50,
                "offset": 0,
                "fields": ["identifier", "orgName", "description", "channel"],
            }
        }
        h_resp = requests.post(f"{base}/api/org/hierarchy/search", json=hierarchy_payload,
                                headers=headers, timeout=10)
        h_resp.raise_for_status()
        orgs = h_resp.json().get("result", {}).get("response", {}).get("content", [])

        org_match = next(
            (o for o in orgs if org_name.lower() in (o.get("orgName") or "").lower()),
            None,
        )

        if not org_match:
            return json.dumps({
                "ministry_or_state_query": ministry_or_state_name,
                "parent_found": True,
                "parent_name": parent_label,
                "found": False,
            })

        return json.dumps({
            "ministry_or_state_query": ministry_or_state_name,
            "parent_found": True,
            "parent_name": parent_label,
            "found": True,
            "org_id": org_match.get("identifier"),
            "org_name": org_match.get("orgName"),
        })
    except Exception as e:
        logger.error(f"[profile_user_management_tools] search_organization_under_ministry_or_state error: {e}")
        return json.dumps({
            "ministry_or_state_query": ministry_or_state_name,
            "found": False,
            "error": str(e),
        })


# ── SOP-P3 STEP 1 — verify the designation against master data ─────────────



_DESIGNATION_CACHE: dict = {"data": None, "fetched_at": None}
_DESIGNATION_CACHE_TTL_SECONDS = 3600


def _fetch_all_designations() -> list[dict]:
    """Fetch (or return cached) full active-designation master list.

    Response shape (confirmed via live curl against the real endpoint):
      {"result": {"result": {"data": [...], "totalCount": N}}} — note the
      DOUBLE-nested "result" key, easy to miss.
    """
    import time

    now = time.time()
    if (
        _DESIGNATION_CACHE["data"] is not None
        and (now - _DESIGNATION_CACHE["fetched_at"]) < _DESIGNATION_CACHE_TTL_SECONDS
    ):
        return _DESIGNATION_CACHE["data"]

    url = f"{IGOT_API_HOST_URL}/apis/public/v8/designation/search"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": "application/json"}
    all_designations: list[dict] = []
    page_size = 100  # confirmed API max — pageSize > 100 returns HTTP 400
    page_number = 1
    total_count = None

    while total_count is None or len(all_designations) < total_count:
        payload = {
            "pageNumber": page_number,
            "pageSize": page_size,
            "filterCriteriaMap": {"status": "Active"},
            "requestedFields": ["id", "designation"],
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        inner = resp.json().get("result", {}).get("result", {})
        page_data = inner.get("data", [])
        total_count = inner.get("totalCount", len(page_data))
        if not page_data:
            break
        all_designations.extend(page_data)
        page_number += 1

    _DESIGNATION_CACHE["data"] = all_designations
    _DESIGNATION_CACHE["fetched_at"] = now
    logger.info(f"[profile_user_management_tools] Cached {len(all_designations)} active designations.")
    return all_designations


@tool
def search_designation(designation_name: str) -> str:
    """Verify a user-reported designation against the platform's full active
    designation master data (cached in-memory for 1 hour — this API has no
    server-side name search, so it fetches everything, paginated, once per
    cache window, and matches locally).

    Used in SOP-P3 STEP 1. Matching is conservative and done here, not left to
    the caller: an exact (case-insensitive, whitespace-normalized) match is a
    single confident hit; anything else that plausibly overlaps by whole word
    is returned as an ambiguous candidate list — never a loose substring guess
    (e.g. "sec officer" must not silently match "Secretary Officer" when
    "Section Officer" was meant).

    Returns match_type: "exact" (one confident match), "ambiguous" (2+
    plausible candidates, the user must confirm which), or "not_found".
    """
    try:
        all_designations = _fetch_all_designations()
    except Exception as e:
        logger.error(f"[profile_user_management_tools] search_designation error: {e}")
        return json.dumps({"query": designation_name, "match_type": "error", "error": str(e)})

    query_norm = " ".join(designation_name.strip().lower().split())
    query_words = set(query_norm.split())

    exact = [
        d for d in all_designations
        if " ".join((d.get("designation") or "").strip().lower().split()) == query_norm
    ]
    if exact:
        d = exact[0]
        return json.dumps({
            "query": designation_name,
            "match_type": "exact",
            "designation": {"id": d.get("id"), "designation": d.get("designation")},
        })

    # Word-PREFIX matching, never mid-word substring: every query word must be
    # a prefix of some whole word in the candidate name (e.g. "sec" is a
    # prefix of "section", catching that as a plausible candidate) — but "sec"
    # must NOT match because it merely appears somewhere inside "Secretary"
    # if it isn't at the start of a word. This is what actually catches
    # abbreviation-style ambiguity (the "sec officer" case) instead of
    # missing it as not_found the way pure whole-word equality would.
    candidates = []
    for d in all_designations:
        name = d.get("designation") or ""
        name_words = list(name.strip().lower().split())
        if query_words and all(
            any(w.startswith(qw) for w in name_words) for qw in query_words
        ):
            candidates.append({"id": d.get("id"), "designation": name})

    if len(candidates) == 1:
        return json.dumps({"query": designation_name, "match_type": "exact", "designation": candidates[0]})
    if candidates:
        # Deliberately NOT including the candidate names here — the caller
        # must never act on or surface any one of them (STEP 1 forbids
        # guessing), so there's nothing for it to do with the actual names.
        return json.dumps({"query": designation_name, "match_type": "ambiguous", "candidate_count": len(candidates)})
    return json.dumps({"query": designation_name, "match_type": "not_found"})


# ── SOP-P3 STEP 2 — the user's own org, and whether it has imported the ────
# confirmed designation (two separate APIs, matching the given SOP exactly)

@tool
def get_user_root_org_id(email: str) -> str:
    """Fetch the user's own rootOrgId via the User Read API.

    Used in SOP-P3 STEP 2, ONLY after a specific designation has already been
    confirmed in STEP 1 — resolves email -> user_id via the standard search,
    then reads the full user profile for rootOrgId.
    """
    search_url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": "application/json"}
    try:
        search_payload = {"request": {"filters": {"email": email}}}
        search_resp = requests.post(search_url, json=search_payload, headers=headers, timeout=10)
        search_resp.raise_for_status()
        content = search_resp.json().get("result", {}).get("response", {}).get("content", [])
        if not content:
            return json.dumps({"found": False, "message": "User profile not found."})

        user_id = content[0].get("id")
        if not user_id:
            return json.dumps({"found": False, "message": "User id not available in profile."})

        read_url = f"{IGOT_API_HOST_URL}/api/user/private/v1/read/{user_id}"
        read_resp = requests.get(read_url, headers=headers, timeout=10)
        read_resp.raise_for_status()
        user_data = read_resp.json().get("result", {}).get("response", {})

        root_org_id = user_data.get("rootOrgId")
        if not root_org_id:
            return json.dumps({"found": False, "message": "rootOrgId not available for this user."})

        return json.dumps({"found": True, "user_id": user_id, "root_org_id": root_org_id})
    except Exception as e:
        logger.error(f"[profile_user_management_tools] get_user_root_org_id error: {e}")
        return json.dumps({"found": False, "error": str(e)})


@tool
def get_org_imported_designations(root_org_id: str) -> str:
    """Fetch the list of designations a specific organisation's MDO has
    imported, via the Org Framework Read API.

    Used in SOP-P3 STEP 2 to check whether the CONFIRMED designation from
    STEP 1 is actually usable by this user's own org yet — existing in the
    platform's master data is not enough, the org must separately import it.

    Response shape (confirmed via live curl against the real endpoint): the
    imported-designations list is NOT a top-level "designation" category (one
    literally coded "designation" exists but is unrelated — its terms have
    zero designation-type associations). It lives inside the "org" category's
    single term (the org itself), under that term's `associations` array,
    filtered to entries with refType == "designation".
    """
    url = f"{IGOT_API_HOST_URL}/api/framework/v1/read/{root_org_id}_odcs"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": "application/json"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        framework = resp.json().get("result", {}).get("framework", {})
        categories = framework.get("categories", [])

        org_category = next((c for c in categories if c.get("code") == "org"), None)
        if not org_category or not org_category.get("terms"):
            return json.dumps({"root_org_id": root_org_id, "found": False,
                                "message": "Org framework/category not found."})

        imported = []
        for org_term in org_category["terms"]:
            for assoc in (org_term.get("associations") or []):
                if assoc.get("refType") == "designation" and assoc.get("name"):
                    imported.append(assoc["name"])

        return json.dumps({
            "root_org_id": root_org_id,
            "found": True,
            "imported_designations": imported,
        })
    except Exception as e:
        logger.error(f"[profile_user_management_tools] get_org_imported_designations error: {e}")
        return json.dumps({"root_org_id": root_org_id, "found": False, "error": str(e)})


# ── Convenience list for the subgraph ─────────────────────────────────────────

def get_profile_user_management_tools() -> list:
    """Return all tools for the ProfileUserManagementSubgraph."""
    from app.core.tools.login_issue_tool import get_yp_am_details

    return [
        get_user_transfer_request_details,  # SOP-A1 STEP 1
        get_mdo_details_by_org_id,          # SOP-A1 STEP 2
        search_organization,                # SOP-A1 Edge Case 2 (exact name)
        search_organization_under_ministry_or_state,  # SOP-A1 Edge Case 2 (Ministry/State + org)
        get_yp_am_details,                  # SOP-A1 STEP 3 / Edge Case 2 YP/SPOC fallback, SOP-P3 Case 2
        search_designation,                 # SOP-P3 STEP 1
        get_user_root_org_id,               # SOP-P3 STEP 2
        get_org_imported_designations,      # SOP-P3 STEP 2
    ]