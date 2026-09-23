"""
Unit tests for app/core/tools/ca_apar_tool.py — tools used by the
CaAparSubgraph (SOP-1, SOP-2, SOP-3).
"""

import json
from unittest.mock import MagicMock, patch

import requests

from app.core.tools.ca_apar_tool import (
    get_assessment_attempt_count,
    get_assigned_cap_courses,
    get_ca_apar_tools,
    get_cap_hierarchy,
    get_org_type,
    get_user_cap_assignment,
    get_user_cbp_plan,
    get_user_enrollments,
    resolve_org_names,
)


def _mock_response(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _search_response(content):
    return _mock_response({"result": {"response": {"content": content}}})


# ── get_user_enrollments (SOP-1 STEP 2, SOP-3 STEP 2/4) ─────────────────────

class TestGetUserEnrollments:
    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_general_listing(self, mock_post):
        search_resp = _search_response([{"id": "user-1"}])
        enroll_resp = _mock_response({
            "result": {"courses": [
                {"contentId": "c1", "enrolledDate": 1, "courseName": "Course 1"},
                {"contentId": "c2", "enrolledDate": 2, "courseName": "Course 2"},
            ]}
        })
        mock_post.side_effect = [search_resp, enroll_resp]

        result = json.loads(get_user_enrollments.func("user@x.com"))

        assert result["total_fetched"] == 2
        # sorted by enrolledDate desc
        assert result["courses"][0]["contentId"] == "c2"

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_content_id_specific_lookup_completed_with_certificate(self, mock_post):
        search_resp = _search_response([{"id": "user-1"}])
        enroll_resp = _mock_response({
            "result": {"courses": [
                {"contentId": "c1", "courseName": "CAP Course", "status": 2, "issuedCertificates": [{"id": "cert-1"}]},
            ]}
        })
        mock_post.side_effect = [search_resp, enroll_resp]

        result = json.loads(get_user_enrollments.func("user@x.com", content_id="c1"))

        assert result["completed"] is True
        assert result["certificate_issued"] is True

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_content_id_specific_lookup_not_enrolled(self, mock_post):
        search_resp = _search_response([{"id": "user-1"}])
        enroll_resp = _mock_response({"result": {"courses": []}})
        mock_post.side_effect = [search_resp, enroll_resp]

        result = json.loads(get_user_enrollments.func("user@x.com", content_id="missing"))

        assert result["completed"] is False
        assert result["certificate_issued"] is False
        assert result["course_name"] is None

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_enrollments.func("nobody@x.com"))

        assert result["error"] == "User not found."

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_user_id_empty(self, mock_post):
        mock_post.return_value = _search_response([{}])

        result = json.loads(get_user_enrollments.func("noid@x.com"))

        assert "user_id is empty" in result["error"]

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_enrollments.func("err@x.com"))

        assert "Error fetching enrollments" in result["error"]


# ── get_user_cbp_plan (SOP-1 STEP 1, Edge Case 1/2) ──────────────────────────

class TestGetUserCbpPlan:
    @patch("app.core.tools.ca_apar_tool.requests.get")
    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_success_splits_apar_and_non_apar(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-1", "firstName": "Asha"}])
        mock_get.return_value = _mock_response({
            "result": {"content": [
                {"id": "p1", "isApar": True, "endDate": "2025-01-01",
                 "contentList": [{"name": "APAR Course", "identifier": "do_1"}]},
                {"id": "p2", "isApar": False, "endDate": "2025-02-01",
                 "contentList": [{"name": "Other Course", "identifier": "do_2"}]},
            ]}
        })

        result = json.loads(get_user_cbp_plan.func("asha@x.com"))

        assert result["found"] is True
        assert result["total_count"] == 2
        assert result["apar_count"] == 1
        assert result["non_apar_count"] == 1
        assert result["apar_plans"][0]["course_name"] == "APAR Course"

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_profile_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_cbp_plan.func("nobody@x.com"))

        assert result["found"] is False
        assert result["message"] == "User profile not found."

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_user_id_missing(self, mock_post):
        mock_post.return_value = _search_response([{}])

        result = json.loads(get_user_cbp_plan.func("user@x.com"))

        assert result["found"] is False
        assert "User id" in result["message"]

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_cbp_plan.func("err@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── get_assigned_cap_courses (SOP-3 STEP 1) ─────────────────────────────────

class TestGetAssignedCapCourses:
    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_success(self, mock_post):
        profile_resp = _search_response([{"id": "user-1", "firstName": "Asha"}])
        assigned_resp = _mock_response({
            "result": {"content": [{"identifier": "cap-1", "name": "CAP 2024", "endDate": "2025-01-01"}]}
        })
        mock_post.side_effect = [profile_resp, assigned_resp]

        result = json.loads(get_assigned_cap_courses.func("asha@x.com"))

        assert result["found"] is True
        assert result["cap_count"] == 1
        assert result["caps"][0]["plan_id"] == "cap-1"

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_profile_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_assigned_cap_courses.func("nobody@x.com"))

        assert result["found"] is False
        assert result["message"] == "User profile not found."

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_user_id_missing(self, mock_post):
        mock_post.return_value = _search_response([{}])

        result = json.loads(get_assigned_cap_courses.func("user@x.com"))

        assert result["found"] is False
        assert "User id" in result["message"]

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_assigned_courses_http_error_is_logged_and_handled(self, mock_post):
        profile_resp = _search_response([{"id": "user-1"}])
        http_error_resp = MagicMock()
        http_error_resp.status_code = 500
        http_error_resp.text = "Internal Server Error"
        http_err = requests.exceptions.HTTPError("server error")
        http_err.response = http_error_resp
        failing_resp = MagicMock()
        failing_resp.raise_for_status.side_effect = http_err
        mock_post.side_effect = [profile_resp, failing_resp]

        result = json.loads(get_assigned_cap_courses.func("user@x.com"))

        assert result["found"] is False
        assert "error" in result

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_assigned_cap_courses.func("err@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── get_cap_hierarchy (SOP-3 STEP 3) ─────────────────────────────────────────

class TestGetCapHierarchy:
    @patch("app.core.tools.ca_apar_tool.requests.get")
    def test_classifies_children_by_resource_type(self, mock_get):
        mock_get.return_value = _mock_response({
            "result": {"content": {
                "name": "CAP 2024",
                "children": [
                    {"identifier": "c1", "name": "Final Assessment", "primaryCategory": "Course Assessment"},
                    {"identifier": "c2", "name": "SCORM Module", "mimeType": "application/vnd.ekstep.html-archive"},
                    {"identifier": "c3", "name": "Video Module", "mimeType": "video/mp4"},
                ],
            }}
        })

        result = json.loads(get_cap_hierarchy.func("cap-1"))

        assert result["found"] is True
        assert result["child_count"] == 3
        by_id = {c["identifier"]: c["resource_type"] for c in result["children"]}
        assert by_id == {"c1": "Assessment", "c2": "SCORM", "c3": "Non-SCORM"}
        assert result["assessment_child_id"] == "c1"

    @patch("app.core.tools.ca_apar_tool.requests.get")
    def test_no_children_found(self, mock_get):
        mock_get.return_value = _mock_response({"result": {"content": {"name": "Empty CAP"}}})

        result = json.loads(get_cap_hierarchy.func("cap-empty"))

        assert result["found"] is False
        assert result["assessment_child_id"] is None

    @patch("app.core.tools.ca_apar_tool.requests.get")
    def test_exception_is_handled(self, mock_get):
        mock_get.side_effect = Exception("timeout")

        result = json.loads(get_cap_hierarchy.func("cap-1"))

        assert result["found"] is False
        assert "error" in result


# ── get_assessment_attempt_count (SOP-3 STEP 5B) ────────────────────────────

class TestGetAssessmentAttemptCount:
    @patch("app.core.tools.ca_apar_tool.requests.get")
    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_remaining_attempts_available(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-1"}])
        mock_get.return_value = _mock_response({"result": {"attemptsMade": 1, "attemptsAllowed": 3}})

        result = json.loads(get_assessment_attempt_count.func("user@x.com", "assess-1"))

        assert result["found"] is True
        assert result["remaining_attempts"] == 2
        assert result["limit_exceeded"] is False

    @patch("app.core.tools.ca_apar_tool.requests.get")
    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_limit_exceeded(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-1"}])
        mock_get.return_value = _mock_response({"result": {"attemptsMade": 3, "attemptsAllowed": 3}})

        result = json.loads(get_assessment_attempt_count.func("user@x.com", "assess-1"))

        assert result["limit_exceeded"] is True

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_assessment_attempt_count.func("nobody@x.com", "assess-1"))

        assert result["found"] is False
        assert result["error"] == "User not found."

    @patch("app.core.tools.ca_apar_tool.requests.get")
    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_missing_attempt_fields(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-1"}])
        mock_get.return_value = _mock_response({"result": {}})

        result = json.loads(get_assessment_attempt_count.func("user@x.com", "assess-1"))

        assert result["found"] is False
        assert "missing" in result["error"]

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_assessment_attempt_count.func("err@x.com", "assess-1"))

        assert result["found"] is False
        assert "error" in result


# ── resolve_org_names (SOP-2) ────────────────────────────────────────────────

class TestResolveOrgNames:
    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_success(self, mock_post):
        mock_post.return_value = _search_response([{"id": "org-1", "orgName": "Ministry X"}])

        result = json.loads(resolve_org_names.func(["org-1"]))

        assert result["resolved"] == [{"id": "org-1", "orgName": "Ministry X"}]

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(resolve_org_names.func(["org-1"]))

        assert result["resolved"] == []
        assert "error" in result


# ── get_org_type (SOP-1 STEP 3) ──────────────────────────────────────────────

class TestGetOrgType:
    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_state_org(self, mock_post):
        mock_post.return_value = _search_response([{"orgName": "State X", "isState": True}])

        result = json.loads(get_org_type.func("root-1"))

        assert result["org_type"] == "state"

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_ministry_org(self, mock_post):
        mock_post.return_value = _search_response([{"orgName": "Ministry X", "isMinistry": True}])

        result = json.loads(get_org_type.func("root-1"))

        assert result["org_type"] == "ministry"

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_falls_back_to_sb_org_type(self, mock_post):
        mock_post.return_value = _search_response([{"orgName": "Org X", "sbOrgType": "SPV"}])

        result = json.loads(get_org_type.func("root-1"))

        assert result["org_type"] == "SPV"

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_unknown_when_no_flags_or_type(self, mock_post):
        mock_post.return_value = _search_response([{"orgName": "Org X"}])

        result = json.loads(get_org_type.func("root-1"))

        assert result["org_type"] == "unknown"

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_org_type.func("root-missing"))

        assert result["found"] is False
        assert result["org_type"] == "unknown"

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_org_type.func("root-1"))

        assert result["found"] is False
        assert result["org_type"] == "unknown"
        assert "error" in result


# ── get_user_cap_assignment (SOP-2 STEP 2) ──────────────────────────────────

class TestGetUserCapAssignment:
    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_success_builds_link_html(self, mock_post):
        profile_resp = _search_response([{"id": "user-1"}])
        cap_resp = _mock_response({
            "result": {"content": [{"identifier": "cap-1", "name": "CAP 2024", "endDate": "2025-01-01"}]}
        })
        mock_post.side_effect = [profile_resp, cap_resp]

        result = json.loads(get_user_cap_assignment.func("user@x.com"))

        assert result["found"] is True
        assert result["total_count"] == 1
        assignment = result["assignments"][0]
        assert assignment["cap_id"] == "cap-1"
        assert "cap-1" in assignment["link"]
        assert "<a href=" in assignment["link_html"]

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_single_dict_content_is_wrapped_in_list(self, mock_post):
        profile_resp = _search_response([{"id": "user-1"}])
        cap_resp = _mock_response({
            "result": {"content": {"identifier": "cap-1", "name": "CAP 2024"}}
        })
        mock_post.side_effect = [profile_resp, cap_resp]

        result = json.loads(get_user_cap_assignment.func("user@x.com"))

        assert result["total_count"] == 1

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_profile_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_cap_assignment.func("nobody@x.com"))

        assert result["found"] is False
        assert result["message"] == "User profile not found."

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_user_id_missing(self, mock_post):
        mock_post.return_value = _search_response([{}])

        result = json.loads(get_user_cap_assignment.func("user@x.com"))

        assert result["found"] is False
        assert "User id" in result["message"]

    @patch("app.core.tools.ca_apar_tool.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_cap_assignment.func("err@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── Convenience list ─────────────────────────────────────────────────────────

class TestGetCaAparTools:
    def test_returns_expected_tools(self):
        tools = get_ca_apar_tools()
        names = {t.name for t in tools}

        assert names == {
            "get_user_cbp_plan",
            "get_assigned_cap_courses",
            "get_user_enrollments",
            "get_user_cap_assignment",
            "get_user_profile",
            "get_mdo_details",
            "get_yp_am_details",
            "get_cap_hierarchy",
            "get_assessment_attempt_count",
            "get_access_settings",
            "resolve_org_names",
            "get_org_type",
        }
