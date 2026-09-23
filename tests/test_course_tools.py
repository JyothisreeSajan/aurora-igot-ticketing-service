"""
Unit tests for app/core/tools/course_tools.py — tools used by the
CoursesSubgraph (SOP-C1 through SOP-C4).
"""

import json
from unittest.mock import MagicMock, patch

from app.core.tools.course_tools import (
    composite_content_search,
    get_access_settings,
    get_apar_assignments,
    get_content_metadata,
    get_course_tools,
    get_mdo_details,
    get_user_enrollments,
    get_user_feed,
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


# ── get_user_enrollments (SOP-C1, C2 STEP 1, C3 STEP 3, C4-C STEP C5) ───────

class TestGetUserEnrollments:
    @patch("app.core.tools.course_tools.requests.post")
    def test_success(self, mock_post):
        search_resp = _search_response([{"id": "user-1"}])
        enrollment_resp = _mock_response({
            "responseCode": "OK",
            "result": {"courses": [{"contentId": "c1", "enrolledDate": 1, "courseName": "Course 1"}]},
        })
        mock_post.side_effect = [search_resp, enrollment_resp]

        result = json.loads(get_user_enrollments.func("user@x.com"))

        assert result["total_fetched"] == 1
        assert result["result"]["courses"][0]["contentId"] == "c1"

    @patch("app.core.tools.course_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_enrollments.func("nobody@x.com"))

        assert result["error"] == "User not found."

    @patch("app.core.tools.course_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_enrollments.func("err@x.com"))

        assert "Error fetching enrollments" in result["error"]


# ── get_user_profile (SOP-C1 STEP 5/6/7/9, SOP-C4-C STEP C1) ────────────────

class TestGetUserProfile:
    @patch("app.core.tools.course_tools.requests.post")
    def test_success(self, mock_post):
        user = {
            "id": "user-1",
            "firstName": "Asha",
            "rootOrgName": "Org Name",
            "rootOrgId": "root-1",
            "profileDetails": {
                "professionalDetails": [
                    {"designation": "Officer", "group": "Group A", "verifiedKarmayogi": True, "profileStatus": "VERIFIED"}
                ]
            },
        }
        mock_post.return_value = _search_response([user])

        result = get_user_profile.func("asha@x.com")

        assert "User Profile:" in result
        assert '"designation": "Officer"' in result

    @patch("app.core.tools.course_tools.requests.post")
    def test_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_profile.func("nobody@x.com"))

        assert result["error"] == "User profile not found."

    @patch("app.core.tools.course_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_profile.func("err@x.com"))

        assert "Error fetching user profile" in result["error"]


# ── composite_content_search (SOP-C1 STEP 3, 8) ─────────────────────────────

class TestCompositeContentSearch:
    @patch("app.core.tools.course_tools.requests.post")
    def test_course_or_program_search(self, mock_post):
        mock_post.return_value = _mock_response({
            "result": {
                "count": 1,
                "content": [{"identifier": "do_1", "name": "Course A", "status": "Live", "source": "Provider"}],
            }
        })

        result = json.loads(composite_content_search.func("Course A"))

        assert result["total_found"] == 1
        assert result["results"][0]["content_id"] == "do_1"
        assert result["results"][0]["provider"] == "Provider"

    @patch("app.core.tools.course_tools.requests.post")
    def test_provider_falls_back_to_organisation_list(self, mock_post):
        mock_post.return_value = _mock_response({
            "result": {
                "content": [{"identifier": "do_2", "name": "Course B", "status": "Live", "organisation": ["Org X"]}],
            }
        })

        result = json.loads(composite_content_search.func("Course B"))

        assert result["results"][0]["provider"] == "Org X"

    @patch("app.core.tools.course_tools.requests.post")
    def test_provider_defaults_to_igot(self, mock_post):
        mock_post.return_value = _mock_response({
            "result": {"content": [{"identifier": "do_3", "name": "Course C", "status": "Live"}]}
        })

        result = json.loads(composite_content_search.func("Course C"))

        assert result["results"][0]["provider"] == "iGOT Karmayogi"

    @patch("app.core.tools.course_tools.requests.post")
    def test_event_type_filters_primary_category(self, mock_post):
        mock_post.return_value = _mock_response({"result": {"content": []}})

        composite_content_search.func("Some Event", "event")

        payload = mock_post.call_args.kwargs["json"]
        assert payload["request"]["filters"]["primaryCategory"] == ["Event"]

    @patch("app.core.tools.course_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = composite_content_search.func("Course A")

        assert "Error searching for" in result


# ── get_access_settings (SOP-C1 STEP 5, 6, 7, 9) ────────────────────────────

class TestGetAccessSettings:
    @patch("app.core.tools.course_tools.requests.get")
    def test_success(self, mock_get):
        mock_get.return_value = _mock_response({"result": {"userGroups": []}})

        result = json.loads(get_access_settings.func("do_1"))

        assert result == {"userGroups": []}

    @patch("app.core.tools.course_tools.requests.get")
    def test_exception_is_handled(self, mock_get):
        mock_get.side_effect = Exception("timeout")

        result = get_access_settings.func("do_1")

        assert "Error fetching access settings" in result


# ── get_mdo_details (SOP-C1 STEP 6B/7, SOP-C4 A3/A4/C-no-MDO) ───────────────

class TestGetMdoDetails:
    # See the equivalent note in tests/test_login_issue_tool.py: course_tools.py
    # and app.core.utils.mdo_lookup share the same `requests` module object,
    # so a single patch + ordered side_effect list must cover both the
    # step-1 profile lookup and the step-2 find_mdo_contact search.
    @patch("app.core.tools.course_tools.requests.post")
    def test_success(self, mock_post):
        admin = {
            "organisations": [{"roles": ["MDO_LEADER"]}],
            "rootOrgName": "Org Name",
            "rootOrgId": "root-1",
            "profileDetails": {
                "personalDetails": {"firstname": "Lead", "primaryEmail": "lead@x.com", "mobile": "9876543210"}
            },
        }
        mock_post.side_effect = [
            _search_response([{"rootOrgId": "root-1"}]),
            _search_response([admin]),
        ]

        result = json.loads(get_mdo_details.func("user@x.com"))

        assert result["found"] is True
        assert result["admins"][0]["rootOrgName"] == "Org Name"

    @patch("app.core.tools.course_tools.requests.post")
    def test_profile_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_mdo_details.func("nobody@x.com"))

        assert result["found"] is False
        assert result["message"] == "User profile not found."

    @patch("app.core.tools.course_tools.requests.post")
    def test_root_org_id_missing(self, mock_post):
        mock_post.return_value = _search_response([{}])

        result = json.loads(get_mdo_details.func("user@x.com"))

        assert result["found"] is False
        assert "rootOrgId" in result["message"]

    @patch("app.core.tools.course_tools.requests.post")
    def test_no_admin_found(self, mock_post):
        mock_post.side_effect = [
            _search_response([{"rootOrgId": "root-1"}]),
            _search_response([]),
            _search_response([]),
        ]

        result = json.loads(get_mdo_details.func("user@x.com"))

        assert result["found"] is False
        assert "root-1" in result["message"]

    @patch("app.core.tools.course_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_mdo_details.func("user@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── get_yp_am_details (SOP-C1 STEP 6B fallback, SOP-C4 C-no-MDO) ────────────

class TestGetYpAmDetails:
    @patch("app.core.tools.course_tools.lookup_yp_by_mdo")
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
        assert result["_spoc_replacements"]["{{YP_AM_NAME}}"] == "Akshay"

    @patch("app.core.tools.course_tools.lookup_yp_by_mdo")
    def test_not_found(self, mock_lookup):
        mock_lookup.return_value = []

        result = json.loads(get_yp_am_details.func("Unknown Org"))

        assert result["found"] is False

    @patch("app.core.tools.course_tools.lookup_yp_by_mdo")
    def test_exception_is_handled(self, mock_lookup):
        mock_lookup.side_effect = Exception("csv read error")

        result = json.loads(get_yp_am_details.func("Some Org"))

        assert result["found"] is False
        assert "error" in result


# ── get_content_metadata (SOP-C2 STEP 3, SOP-C3 STEP 3) ─────────────────────

class TestGetContentMetadata:
    @patch("app.core.tools.course_tools.requests.post")
    def test_success(self, mock_post):
        mock_post.return_value = _mock_response({
            "result": {"content": [{"identifier": "do_1", "name": "Course A", "contentType": "Course"}]}
        })

        result = json.loads(get_content_metadata.func("do_1"))

        assert result["content_id"] == "do_1"
        assert result["contentType"] == "Course"

    @patch("app.core.tools.course_tools.requests.post")
    def test_not_found(self, mock_post):
        mock_post.return_value = _mock_response({"result": {"content": []}})

        result = json.loads(get_content_metadata.func("do_missing"))

        assert result["found"] is False

    @patch("app.core.tools.course_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = get_content_metadata.func("do_1")

        assert "Error fetching content metadata" in result


# ── get_user_feed (SOP-C4 SECTION A STEP A1) ────────────────────────────────

class TestGetUserFeed:
    @patch("app.core.tools.course_tools.requests.post")
    def test_success(self, mock_post):
        user = {
            "id": "user-1",
            "status": 1,
            "userType": "OFFICIAL",
            "channel": "channel",
            "rootOrgId": "root-1",
            "rootOrgName": "Org Name",
            "externalIds": [{"idType": "eHRMS", "id": "ehrms-1"}],
            "profileDetails": {
                "professionalDetails": [{"designation": "Officer", "group": "Group A", "profileStatus": "VERIFIED"}],
                "employmentDetails": {"employeeCode": "EMP1"},
            },
        }
        mock_post.return_value = _search_response([user])

        result = json.loads(get_user_feed.func("user@x.com"))

        assert result["id"] == "user-1"
        assert result["externalIds"][0]["id"] == "ehrms-1"
        assert result["profileDetails"]["employmentDetails"]["employeeCode"] == "EMP1"

    @patch("app.core.tools.course_tools.requests.post")
    def test_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_feed.func("nobody@x.com"))

        assert result["error"] == "User feed not found."

    @patch("app.core.tools.course_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_feed.func("err@x.com"))

        assert "Error fetching user feed" in result["error"]


# ── get_apar_assignments (SOP-C4 SECTION C STEP C5) ─────────────────────────

class TestGetAparAssignments:
    @patch("app.core.tools.course_tools.requests.post")
    def test_apar_assigned_and_passed(self, mock_post):
        search_resp = _search_response([{"id": "user-1"}])
        enrollment_resp = _mock_response({
            "result": {
                "courses": [
                    {
                        "courseId": "c1",
                        "courseName": "APAR Assessment 2024",
                        "contentId": "do_1",
                        "status": 2,
                        "issuedCertificates": [{"id": "cert-1"}],
                        "enrolledDate": 1,
                    },
                    {"courseId": "c2", "courseName": "Unrelated Course", "status": 1, "enrolledDate": 2},
                ]
            }
        })
        mock_post.side_effect = [search_resp, enrollment_resp]

        result = json.loads(get_apar_assignments.func("user@x.com"))

        assert result["apar_assigned"] is True
        assert result["cap_assessment_passed"] is True
        assert len(result["assignments"]) == 1
        assert result["assignments"][0]["courseId"] == "c1"

    @patch("app.core.tools.course_tools.requests.post")
    def test_no_apar_courses(self, mock_post):
        search_resp = _search_response([{"id": "user-1"}])
        enrollment_resp = _mock_response({"result": {"courses": [{"courseName": "Unrelated", "status": 1}]}})
        mock_post.side_effect = [search_resp, enrollment_resp]

        result = json.loads(get_apar_assignments.func("user@x.com"))

        assert result["apar_assigned"] is False
        assert result["cap_assessment_passed"] is False

    @patch("app.core.tools.course_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_apar_assignments.func("nobody@x.com"))

        assert "error" in result

    @patch("app.core.tools.course_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = get_apar_assignments.func("err@x.com")

        assert "Error fetching APAR assignments" in result


# ── Convenience list ─────────────────────────────────────────────────────────

class TestGetCourseTools:
    def test_returns_expected_tools(self):
        tools = get_course_tools()
        names = {t.name for t in tools}

        assert names == {
            "composite_content_search",
            "get_access_settings",
            "get_user_profile",
            "get_mdo_details",
            "get_yp_am_details",
            "get_user_enrollments",
            "get_content_metadata",
            "get_user_feed",
            "get_apar_assignments",
        }
