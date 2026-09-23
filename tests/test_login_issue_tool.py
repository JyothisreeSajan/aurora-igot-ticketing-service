"""
Unit tests for app/core/tools/login_issue_tool.py — tools used by the
LoginAndRegistrationSubgraph (SOP-L1, SOP-L2).
"""

import json
from unittest.mock import MagicMock, patch

from app.core.tools.login_issue_tool import (
    get_mdo_details,
    get_user_enrollments,
    get_user_profile,
    get_user_transfer_request,
    get_yp_am_details,
    lookup_user_by_contact,
    validate_email_domain,
)


def _mock_response(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _search_response(content):
    return _mock_response({"result": {"response": {"content": content}}})


# ── get_user_profile (SOP-L1 STEP 3A, SOP-L2 STEP 1) ────────────────────────

class TestGetUserProfile:
    @patch("app.core.tools.login_issue_tool.requests.get")
    def test_success(self, mock_get):
        mock_get.return_value = _mock_response({
            "result": {
                "response": {
                    "id": "user-1",
                    "rootOrgId": "root-1",
                    "rootOrgName": "Org Name",
                    "channel": "org-channel",
                    "status": 1,
                    "userType": "OFFICIAL",
                    "roles": ["PUBLIC"],
                    "profileDetails": {
                        "professionalDetails": [
                            {
                                "designation": "Officer",
                                "group": "Group A",
                                "ministryOrStateOrgName": "Ministry X",
                                "verifiedKarmayogi": True,
                            }
                        ]
                    },
                }
            }
        })

        result = get_user_profile.func("user-1")

        assert "User Profile for ID user-1" in result
        assert '"designation": "Officer"' in result
        assert '"rootOrgId": "root-1"' in result

    @patch("app.core.tools.login_issue_tool.requests.get")
    def test_not_found(self, mock_get):
        mock_get.return_value = _mock_response({"result": {"response": {}}})

        result = get_user_profile.func("missing-id")

        assert "not found" in result

    @patch("app.core.tools.login_issue_tool.requests.get")
    def test_exception_is_handled(self, mock_get):
        mock_get.side_effect = Exception("timeout")

        result = get_user_profile.func("user-1")

        assert "Error fetching user details" in result


# ── validate_email_domain (SOP-L1 STEP 3) ───────────────────────────────────

class TestValidateEmailDomain:
    @patch("app.core.tools.login_issue_tool.requests.get")
    def test_whitelisted(self, mock_get):
        mock_get.return_value = _mock_response({"result": {"domains": ["gov.in"]}})

        result = validate_email_domain.func("user@GOV.IN")

        assert "is whitelisted" in result

    @patch("app.core.tools.login_issue_tool.requests.get")
    def test_not_whitelisted(self, mock_get):
        mock_get.return_value = _mock_response({"result": {"domains": ["gov.in"]}})

        result = validate_email_domain.func("user@gmail.com")

        assert "NOT whitelisted" in result

    @patch("app.core.tools.login_issue_tool.requests.get")
    def test_exception_is_handled(self, mock_get):
        mock_get.side_effect = Exception("timeout")

        result = validate_email_domain.func("user@gov.in")

        assert "Error validating email domain" in result


# ── lookup_user_by_contact (SOP-L1 STEP 4, 6) ───────────────────────────────

class TestLookupUserByContact:
    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_registered(self, mock_post):
        mock_post.return_value = _search_response([{
            "id": "user-2",
            "rootOrgId": "root-2",
            "rootOrgName": "Org Name",
            "channel": "channel",
            "status": 1,
            "userType": "OFFICIAL",
            "organisations": [],
        }])

        result = json.loads(lookup_user_by_contact.func("taken@x.com"))

        assert result["is_registered"] is True
        assert result["user"]["user_id"] == "user-2"

    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_not_registered(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(lookup_user_by_contact.func("free@x.com"))

        assert result["is_registered"] is False
        assert result["user"] is None

    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = lookup_user_by_contact.func("err@x.com")

        assert "Error looking up user by email" in result


# ── get_user_enrollments (SOP-L1 STEP 6) ────────────────────────────────────

class TestGetUserEnrollments:
    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_success_both_statuses(self, mock_post):
        search_resp = _search_response([{"id": "user-1"}])
        enrollment_resp = _mock_response({
            "responseCode": "OK",
            "result": {
                "courses": [
                    {"contentId": "c1", "enrolledDate": 2, "courseName": "Course 1"},
                    {"contentId": "c2", "enrolledDate": 1, "courseName": "Course 2"},
                ]
            },
        })
        mock_post.side_effect = [search_resp, enrollment_resp]

        result = json.loads(get_user_enrollments.func("user@x.com"))

        assert result["total_fetched"] == 2
        assert result["returned"] == 2
        assert result["result"]["courses"][0]["contentId"] == "c1"

    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_status_filter_in_progress(self, mock_post):
        search_resp = _search_response([{"id": "user-1"}])
        enrollment_resp = _mock_response({"result": {"courses": []}})
        mock_post.side_effect = [search_resp, enrollment_resp]

        get_user_enrollments.func("user@x.com", "In-Progress")

        enrollment_call = mock_post.call_args_list[1]
        assert enrollment_call.kwargs["json"]["request"]["status"] == ["In-Progress"]

    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_enrollments.func("nobody@x.com"))

        assert result["error"] == "User not found."

    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_user_id_empty(self, mock_post):
        mock_post.return_value = _search_response([{}])

        result = json.loads(get_user_enrollments.func("noid@x.com"))

        assert "user_id is empty" in result["error"]

    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_enrollments.func("err@x.com"))

        assert "Error fetching enrollments" in result["error"]


# ── get_user_transfer_request (SOP-L2 STEP 1) ───────────────────────────────

class TestGetUserTransferRequest:
    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_found_with_transfer_request(self, mock_post):
        mock_post.return_value = _search_response([{"wfTransferRequest": {"wfId": "wf-1"}}])

        result = json.loads(get_user_transfer_request.func("user-1"))

        assert result["found"] is True
        assert result["wfTransferRequest"]["wfId"] == "wf-1"

    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_transfer_request.func("missing-id"))

        assert result["found"] is False
        assert result["wfTransferRequest"] is None

    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_transfer_request.func("user-1"))

        assert result["found"] is False
        assert "error" in result


# ── get_mdo_details (SOP-L1 STEP 3A/5A, SOP-L2 STEP 2A) ─────────────────────

class TestGetMdoDetails:
    # NOTE: login_issue_tool.py and app.core.utils.mdo_lookup both do a plain
    # `import requests`, so they share the exact same `requests` module object
    # — `requests.post` is ONE attribute regardless of which module's dotted
    # path patches it. Patching both paths separately just overwrites the
    # same attribute twice (only the last-applied patch is actually live for
    # every call site). So get_mdo_details, which makes a step-1 profile
    # lookup directly and then a step-2 MDO search via find_mdo_contact, must
    # be tested with a SINGLE patch and a side_effect list covering both
    # steps in call order.
    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_success(self, mock_post):
        admin = {
            "organisations": [{"roles": ["MDO_LEADER"]}],
            "rootOrgName": "Org Name",
            "rootOrgId": "root-1",
            "profileDetails": {
                "personalDetails": {
                    "firstname": "Lead",
                    "primaryEmail": "lead@x.com",
                    "mobile": "9876543210",
                    "ministryOrStateOrgName": "Ministry X",
                }
            },
        }
        mock_post.side_effect = [
            _search_response([{"rootOrgId": "root-1"}]),  # step 1: profile lookup
            _search_response([admin]),                     # step 2: MDO_LEADER search
        ]

        result = json.loads(get_mdo_details.func("user@x.com"))

        assert result["found"] is True
        assert result["admins"][0]["rootOrgName"] == "Org Name"
        assert result["_spoc_replacements"]["{{MDO_ADMIN_NAME}}"] == "Lead"

    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_profile_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_mdo_details.func("nobody@x.com"))

        assert result["found"] is False
        assert result["message"] == "User profile not found."

    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_root_org_id_missing(self, mock_post):
        mock_post.return_value = _search_response([{}])

        result = json.loads(get_mdo_details.func("user@x.com"))

        assert result["found"] is False
        assert "rootOrgId" in result["message"]

    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_no_admin_found(self, mock_post):
        mock_post.side_effect = [
            _search_response([{"rootOrgId": "root-1"}]),  # step 1: profile lookup
            _search_response([]),                          # step 2: MDO_LEADER search
            _search_response([]),                          # step 2: MDO_ADMIN fallback
        ]

        result = json.loads(get_mdo_details.func("user@x.com"))

        assert result["found"] is False
        assert "root-1" in result["message"]

    @patch("app.core.tools.login_issue_tool.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_mdo_details.func("user@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── get_yp_am_details (fallback across multiple SOPs) ───────────────────────

class TestGetYpAmDetails:
    @patch("app.core.utils.helpers.lookup_yp_by_mdo")
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
        assert result["count"] == 1
        assert result["_spoc_replacements"]["{{YP_AM_NAME}}"] == "Akshay"

    @patch("app.core.utils.helpers.lookup_yp_by_mdo")
    def test_not_found(self, mock_lookup):
        mock_lookup.return_value = []

        result = json.loads(get_yp_am_details.func("Unknown Org"))

        assert result["found"] is False
        assert result["results"] == []

    @patch("app.core.utils.helpers.lookup_yp_by_mdo")
    def test_exception_is_handled(self, mock_lookup):
        mock_lookup.side_effect = Exception("csv read error")

        result = json.loads(get_yp_am_details.func("Some Org"))

        assert result["found"] is False
        assert "error" in result


# ── Convenience list ─────────────────────────────────────────────────────────

class TestGetLoginTools:
    def test_returns_expected_tools(self):
        from app.core.tools.login_issue_tool import get_login_tools

        tools = get_login_tools()
        names = {t.name for t in tools}

        assert names == {
            "get_user_profile",
            "validate_email_domain",
            "lookup_user_by_contact",
            "get_user_enrollments",
            "get_user_transfer_request",
            "get_mdo_details",
            "get_yp_am_details",
        }
