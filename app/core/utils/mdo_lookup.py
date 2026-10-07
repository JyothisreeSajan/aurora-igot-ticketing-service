"""
app/core/utils/mdo_lookup.py
-----------------------------
Shared MDO point-of-contact resolution helper.

`organisations.roles` accepts a list, but a combined single-call filter of
["MDO_LEADER", "MDO_ADMIN"] has been observed to AND-match (requiring both
roles on the same org entry) rather than OR-match — an org with only an
MDO_LEADER (no separate MDO_ADMIN) then comes back with zero results even
though a valid contact exists. To stay correct regardless of that filter's
match semantics, `find_mdo_contact` issues one single-role search at a time:
MDO_LEADER first, MDO_ADMIN only if that came back empty.
"""

import requests

MDO_LEADER = "MDO_LEADER"
MDO_ADMIN = "MDO_ADMIN"


def _has_role(entry: dict, role: str) -> bool:
    return any(
        role in (org.get("roles") or [])
        for org in entry.get("organisations") or []
    )


def pick_mdo_entry(content_list: list[dict]) -> tuple[dict | None, str | None]:
    """Pick the highest-priority MDO contact from a User Search API content list.

    Scans for the first entry whose organisations[].roles contains
    MDO_LEADER; if none exists, falls back to the first entry containing
    MDO_ADMIN.

    Returns:
        (winning_entry, matched_role) — matched_role is "MDO_LEADER" or
        "MDO_ADMIN". (None, None) when neither role is present in the list.
    """
    for entry in content_list or []:
        if _has_role(entry, MDO_LEADER):
            return entry, MDO_LEADER
    for entry in content_list or []:
        if _has_role(entry, MDO_ADMIN):
            return entry, MDO_ADMIN
    return None, None


def _search_by_role(url: str, headers: dict, role: str, extra_filters: dict, timeout: int = 10) -> list[dict]:
    payload = {
        "request": {
            "filters": {
                **extra_filters,
                "organisations.roles": [role],
                "status": 1,
            },
            "limit": 10,
        }
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("response", {}).get("content", [])


def _find_mdo_contact(
    url: str, headers: dict, extra_filters: dict, timeout: int = 10
) -> tuple[dict | None, str | None, int]:
    for role in (MDO_LEADER, MDO_ADMIN):
        content = _search_by_role(url, headers, role, extra_filters, timeout=timeout)
        entry, matched_role = pick_mdo_entry(content)
        if entry is not None:
            return entry, matched_role, len(content)
    return None, None, 0


def find_mdo_contact(
    url: str, headers: dict, org_id: str, timeout: int = 10
) -> tuple[dict | None, str | None, int]:
    """Find the org's MDO point of contact by rootOrgId: MDO_LEADER preferred,
    MDO_ADMIN fallback.

    Makes up to two API calls, each filtered by a single role, and returns
    as soon as one yields a match. Returns (entry, matched_role, count) —
    count is the number of active holders of matched_role found for the org.
    (None, None, 0) when neither role has an active holder.
    """
    return _find_mdo_contact(url, headers, {"rootOrgId": org_id}, timeout=timeout)


def find_mdo_contact_by_channel(
    url: str, headers: dict, channel: str, timeout: int = 10
) -> tuple[dict | None, str | None, int]:
    """Find a department's MDO point of contact by its channel name: MDO_LEADER
    preferred, MDO_ADMIN fallback. Same two-call, single-role-at-a-time
    approach as find_mdo_contact, filtered by channel instead of rootOrgId.
    """
    return _find_mdo_contact(url, headers, {"channel": channel}, timeout=timeout)


def build_mdo_details_response(admin: dict | None, match_count: int, root_org_id: str) -> dict:
    """Build the get_mdo_details response dict from a find_mdo_contact result.

    Shared by login_issue_tool.get_mdo_details and ca_apar_tool.get_mdo_details.
    admin=None produces the not-found shape; otherwise the full
    admins/_spoc_replacements shape with masked contact placeholder tokens.
    """
    if admin is None:
        return {
            "root_org_id": root_org_id,
            "found": False,
            "message": f"No active MDO Admin found for organisation '{root_org_id}'.",
        }

    pd = admin.get("profileDetails", {})
    personal = pd.get("personalDetails", {})

    real_name = personal.get("firstname", "MDO Admin")
    real_email = personal.get("primaryEmail", "")

    spoc_replacements = {}
    if real_name:
        spoc_replacements["{{MDO_ADMIN_NAME}}"] = real_name
    if real_email:
        spoc_replacements["{{MDO_ADMIN_EMAIL}}"] = real_email

    admins = [
        {
            "rootOrgName":            admin.get("rootOrgName", ""),
            "rootOrgId":              admin.get("rootOrgId", ""),
            "mdo_admin_name":         "{{MDO_ADMIN_NAME}}",
            "mdo_admin_email":        "{{MDO_ADMIN_EMAIL}}",
            "ministryOrStateOrgName": pd.get("ministryOrStateOrgName", ""),
            "ministryOrStateType":    pd.get("ministryOrStateType", ""),
            "profileStatus":          pd.get("profileStatus", ""),
        }
    ]

    return {
        "root_org_id": root_org_id,
        "found":       True,
        "count":       match_count,
        "admins":      admins,
        "_spoc_replacements": spoc_replacements,
    }
