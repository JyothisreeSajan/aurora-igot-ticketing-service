"""
Unit tests for the SOP-A3 (Profile Verification / Designation-Group badge)
tools in app/core/tools/profile_user_management_tools.py:
  - get_profile_verification_request_details (STEP 1)
  - get_department_mdo_admin (STEP 2)
"""

import json
from unittest.mock import MagicMock, patch

from app.core.tools.profile_user_management_tools import (
    get_department_mdo_admin,
    get_profile_verification_request_details,
)


def _mock_response(content):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"result": {"response": {"content": content}}}
    return resp


class TestGetProfileVerificationRequestDetails:
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _mock_response([])

        result = json.loads(get_profile_verification_request_details.func("nobody@x.com"))

        assert result["found"] is False

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_no_pending_request(self, mock_post):
        user = {
            "firstName": "Asha",
            "profileDetails": {
                "profileStatus": "VERIFIED",
                "profileDesignationStatus": "VERIFIED",
                "profileGroupStatus": "VERIFIED",
                "professionalDetails": [{"designation": "Officer", "group": "Group A", "name": "Dept X"}],
            },
            "wfProfileDesignationRequest": {},
            "wfProfileGroupRequest": {},
        }
        mock_post.return_value = _mock_response([user])

        result = json.loads(get_profile_verification_request_details.func("asha@x.com"))

        assert result["found"] is True
        assert result["has_designation_request"] is False
        assert result["has_group_request"] is False
        assert result["has_pending_request"] is False
        assert result["pending_department_name"] is None
        assert result["designation"] == "Officer"
        assert result["group"] == "Group A"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_designation_request_pending_takes_priority_over_group(self, mock_post):
        user = {
            "firstName": "Ravi",
            "profileDetails": {
                "profileStatus": "NOT_VERIFIED",
                "profileDesignationStatus": "PENDING",
                "profileGroupStatus": "PENDING",
                "professionalDetails": [{"designation": "Clerk", "group": "Group B", "name": "Dept Y"}],
            },
            "wfProfileDesignationRequest": {"wfId": "wf-1", "departmentName": "Designation Dept"},
            "wfProfileGroupRequest": {"wfId": "wf-2", "departmentName": "Group Dept"},
        }
        mock_post.return_value = _mock_response([user])

        result = json.loads(get_profile_verification_request_details.func("ravi@x.com"))

        assert result["has_designation_request"] is True
        assert result["has_group_request"] is True
        assert result["has_pending_request"] is True
        assert result["pending_department_name"] == "Designation Dept"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_group_request_used_when_designation_request_absent(self, mock_post):
        user = {
            "firstName": "Meera",
            "profileDetails": {},
            "wfProfileDesignationRequest": {},
            "wfProfileGroupRequest": {"wfId": "wf-2", "departmentName": "Group Dept"},
        }
        mock_post.return_value = _mock_response([user])

        result = json.loads(get_profile_verification_request_details.func("meera@x.com"))

        assert result["has_designation_request"] is False
        assert result["has_group_request"] is True
        assert result["pending_department_name"] == "Group Dept"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_department_name_falls_back_to_channel(self, mock_post):
        user = {
            "firstName": "Zoya",
            "channel": "channel-org",
            "profileDetails": {"professionalDetails": []},
            "wfProfileDesignationRequest": {},
            "wfProfileGroupRequest": {},
        }
        mock_post.return_value = _mock_response([user])

        result = json.loads(get_profile_verification_request_details.func("zoya@x.com"))

        assert result["department_name"] == "channel-org"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_request_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_profile_verification_request_details.func("err@x.com"))

        assert result["found"] is False
        assert "error" in result


class TestGetDepartmentMdoAdmin:
    @patch("app.core.utils.mdo_lookup.requests.post")
    def test_prefers_mdo_leader(self, mock_post):
        leader = {
            "organisations": [{"roles": ["MDO_LEADER"]}],
            "profileDetails": {
                "personalDetails": {"firstname": "Lead", "surname": "Er", "primaryEmail": "lead@x.com"}
            },
        }
        mock_post.return_value = _mock_response([leader])

        result = json.loads(get_department_mdo_admin.func("Some Department"))

        assert result["found"] is True
        assert result["department_name"] == "Some Department"
        assert result["_spoc_replacements"]["{{MDO_ADMIN_NAME}}"] == "Lead Er"
        assert result["_spoc_replacements"]["{{MDO_ADMIN_EMAIL}}"] == "lead@x.com"

    @patch("app.core.utils.mdo_lookup.requests.post")
    def test_not_found_when_no_leader_or_admin(self, mock_post):
        mock_post.side_effect = [_mock_response([]), _mock_response([])]

        result = json.loads(get_department_mdo_admin.func("Empty Department"))

        assert result["found"] is False
        assert "Empty Department" in result["message"]

    @patch("app.core.utils.mdo_lookup.requests.post")
    def test_lookup_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("boom")

        result = json.loads(get_department_mdo_admin.func("Dept"))

        assert result["found"] is False
        assert "error" in result
