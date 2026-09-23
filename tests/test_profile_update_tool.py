"""
Unit tests for app/core/tools/profile_update_tool.py — tools used by the
ProfileUpdateSubgraph (SOP-P1).
"""

import json
from unittest.mock import MagicMock, patch

from app.core.tools.profile_update_tool import (
    get_org_admin_details,
    get_profile_update_tools,
    get_user_profile,
    get_yp_am_details,
)


def _mock_response(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _search_response(content):
    return _mock_response({"result": {"response": {"content": content}}})


# ── get_user_profile (SOP-P1 STEP 1) ────────────────────────────────────────

class TestGetUserProfile:
    @patch("app.core.tools.profile_update_tool.requests.post")
    def test_success(self, mock_post):
        user = {
            "id": "user-1",
            "firstName": "Asha",
            "rootOrgName": "Org Name",
            "rootOrgId": "root-1",
            "channel": "channel",
            "profileDetails": {
                "profileDesignationStatus": "PENDING",
                "profileStatus": "NOT_VERIFIED",
                "professionalDetails": [
                    {
                        "designation": "Officer",
                        "group": "Group A",
                        "verifiedKarmayogi": False,
                        "profileStatus": "NOT_VERIFIED",
                        "department": "Dept X",
                        "ministry": "Ministry X",
                    }
                ],
            },
        }
        mock_post.return_value = _search_response([user])

        result = json.loads(get_user_profile.func("asha@x.com"))

        assert result["id"] == "user-1"
        assert result["profileDetails"]["profileDesignationStatus"] == "PENDING"
        assert result["profileDetails"]["professionalDetails"][0]["designation"] == "Officer"
        assert result["_spoc_replacements"]["{{USER_EMAIL}}"] == "asha@x.com"

    @patch("app.core.tools.profile_update_tool.requests.post")
    def test_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_profile.func("nobody@x.com"))

        assert result["error"] == "User profile not found."

    @patch("app.core.tools.profile_update_tool.requests.post")
    def test_profile_details_non_dict_is_tolerated(self, mock_post):
        user = {"id": "user-1", "profileDetails": None}
        mock_post.return_value = _search_response([user])

        result = json.loads(get_user_profile.func("user@x.com"))

        assert result["id"] == "user-1"
        assert result["profileDetails"]["professionalDetails"][0]["designation"] is None

    @patch("app.core.tools.profile_update_tool.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_profile.func("err@x.com"))

        assert "Error fetching user profile" in result["error"]


# ── get_org_admin_details (SOP-P1 STEP 3) ───────────────────────────────────

class TestGetOrgAdminDetails:
    @patch("app.core.utils.mdo_lookup.requests.post")
    def test_success(self, mock_post):
        admin = {
            "organisations": [{"roles": ["MDO_LEADER"]}],
            "rootOrgName": "Org Name",
            "rootOrgId": "root-1",
            "profileDetails": {
                "personalDetails": {"firstname": "Lead", "primaryEmail": "lead@x.com", "mobile": "9876543210"}
            },
        }
        mock_post.return_value = _search_response([admin])

        result = json.loads(get_org_admin_details.func("root-1"))

        assert result["found"] is True
        assert result["org_admin_name"] == "{{MDO_ADMIN_NAME}}"
        assert result["_spoc_replacements"]["{{MDO_ADMIN_NAME}}"] == "Lead"

    @patch("app.core.utils.mdo_lookup.requests.post")
    def test_not_found(self, mock_post):
        mock_post.side_effect = [_search_response([]), _search_response([])]

        result = json.loads(get_org_admin_details.func("root-empty"))

        assert result["found"] is False
        assert "root-empty" in result["message"]

    @patch("app.core.utils.mdo_lookup.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("boom")

        result = json.loads(get_org_admin_details.func("root-1"))

        assert result["found"] is False
        assert "error" in result


# ── get_yp_am_details (SOP-P1 STEP 3B + global fallback) ────────────────────

class TestGetYpAmDetails:
    @patch("app.core.tools.profile_update_tool.lookup_yp_by_mdo")
    def test_found(self, mock_lookup):
        mock_lookup.return_value = [{
            "centre_state": "Centre",
            "mdo": "Department of Atomic Energy",
            "spoc": "Akshay",
            "email": "akshay@x.com",
            "mobile": "9910210521",
            "yp_email": "yp@x.com",
        }]

        result = json.loads(get_yp_am_details.func("atomic energy"))

        assert result["found"] is True
        assert result["yp_am_name"] == "{{YP_AM_NAME}}"
        assert result["_spoc_replacements"]["{{YP_AM_EMAIL}}"] == "akshay@x.com"

    @patch("app.core.tools.profile_update_tool.lookup_yp_by_mdo")
    def test_not_found(self, mock_lookup):
        mock_lookup.return_value = []

        result = json.loads(get_yp_am_details.func("Unknown Org"))

        assert result["found"] is False
        assert result["results"] == []

    @patch("app.core.tools.profile_update_tool.lookup_yp_by_mdo")
    def test_exception_is_handled(self, mock_lookup):
        mock_lookup.side_effect = Exception("csv read error")

        result = json.loads(get_yp_am_details.func("Some Org"))

        assert result["found"] is False
        assert "error" in result


# ── Convenience list ─────────────────────────────────────────────────────────

class TestGetProfileUpdateTools:
    def test_returns_expected_tools(self):
        tools = get_profile_update_tools()
        names = {t.name for t in tools}

        assert names == {
            "get_user_profile",
            "get_org_admin_details",
            "get_yp_am_details",
        }
