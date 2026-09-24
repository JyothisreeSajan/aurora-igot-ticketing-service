"""
Unit tests for app/core/tools/enrolment_tools.py — tools used by
ContentRelatedSubgraph's "Enrolment Issues" flow (content_related_issue ->
enrolment_issues: unable to find/enroll in a course, program, moderated
course, or event).
"""

import json
from unittest.mock import MagicMock, patch

from app.core.tools.enrolment_tools import (
    USER_PROFILE_NOT_FOUND_MESSAGE,
    _check_secure_settings_eligibility,
    _check_user_eligibility,
    _criteria_group_matches,
    check_course_or_event_access,
    get_enrolment_tools,
    get_mdo_admin,
    get_user_eligibility_profile,
    get_yp_am_contact,
    search_course_or_program,
    search_event,
)


def _mock_response(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _search_response(content):
    return _mock_response({"result": {"response": {"content": content}}})


def _profile_response(**overrides):
    base = {
        "id": "user-1",
        "firstName": "Asha",
        "rootOrgId": "root-1",
        "rootOrgName": "Ministry of Test",
        "channel": "org-channel",
        "profileDetails": {
            "professionalDetails": [
                {"group": "Group A", "designation": "Officer"},
            ],
            "employmentDetails": {"departmentName": "Dept X"},
            "cadreDetails": {
                "cadreName": "IAS",
                "civilServiceName": "IAS",
                "cadreBatch": 2015,
            },
            "profileStatus": "VERIFIED",
            "ministryOrStateId": "ministry-1",
        },
    }
    base.update(overrides)
    return _mock_response({"result": {"response": base}})


# ── _criteria_group_matches ──────────────────────────────────────────────────

class TestCriteriaGroupMatches:
    def test_scalar_ctx_value_matches(self):
        criteria = [{"criteriaKey": "cadre", "criteriaValue": ["IAS"]}]
        assert _criteria_group_matches(criteria, {"cadre": "IAS"}) is True

    def test_scalar_ctx_value_not_in_values(self):
        criteria = [{"criteriaKey": "cadre", "criteriaValue": ["IPS"]}]
        assert _criteria_group_matches(criteria, {"cadre": "IAS"}) is False

    def test_list_ctx_value_any_match(self):
        criteria = [{"criteriaKey": "group", "criteriaValue": ["Group B"]}]
        ctx = {"group": ["Group A", "Group B"]}
        assert _criteria_group_matches(criteria, ctx) is True

    def test_list_ctx_value_no_match(self):
        criteria = [{"criteriaKey": "group", "criteriaValue": ["Group C"]}]
        ctx = {"group": ["Group A", "Group B"]}
        assert _criteria_group_matches(criteria, ctx) is False

    def test_missing_ctx_key_returns_false(self):
        criteria = [{"criteriaKey": "department", "criteriaValue": ["Dept X"]}]
        assert _criteria_group_matches(criteria, {}) is False

    def test_empty_criteria_list_is_vacuously_true(self):
        assert _criteria_group_matches([], {"cadre": "IAS"}) is True

    def test_all_criteria_in_group_must_match(self):
        criteria = [
            {"criteriaKey": "cadre", "criteriaValue": ["IAS"]},
            {"criteriaKey": "department", "criteriaValue": ["Dept X"]},
        ]
        ctx = {"cadre": "IAS", "department": "Dept Y"}
        assert _criteria_group_matches(criteria, ctx) is False

    def test_scalar_criteria_value_is_wrapped(self):
        criteria = [{"criteriaKey": "cadre", "criteriaValue": "IAS"}]
        assert _criteria_group_matches(criteria, {"cadre": "IAS"}) is True

    def test_key_field_fallback(self):
        criteria = [{"key": "cadre", "value": ["IAS"]}]
        assert _criteria_group_matches(criteria, {"cadre": "IAS"}) is True


# ── _check_user_eligibility ──────────────────────────────────────────────────

class TestCheckUserEligibility:
    def test_no_user_groups_is_publicly_eligible(self):
        assert _check_user_eligibility([], {}) is True

    def test_eligible_when_any_group_matches(self):
        groups = [
            {"userGroupCriteriaList": [{"criteriaKey": "cadre", "criteriaValue": ["IPS"]}]},
            {"userGroupCriteriaList": [{"criteriaKey": "cadre", "criteriaValue": ["IAS"]}]},
        ]
        assert _check_user_eligibility(groups, {"cadre": "IAS"}) is True

    def test_not_eligible_when_no_group_matches(self):
        groups = [{"userGroupCriteriaList": [{"criteriaKey": "cadre", "criteriaValue": ["IPS"]}]}]
        assert _check_user_eligibility(groups, {"cadre": "IAS"}) is False


# ── _check_secure_settings_eligibility ───────────────────────────────────────

class TestCheckSecureSettingsEligibility:
    def test_not_a_dict_is_eligible(self):
        assert _check_secure_settings_eligibility(None, {}) is True

    def test_no_organisation_restriction_and_no_verification_required(self):
        assert _check_secure_settings_eligibility({}, {"rootOrgId": "root-1"}) is True

    def test_organisation_match_via_root_org_id(self):
        settings = {"organisation": ["root-1"]}
        assert _check_secure_settings_eligibility(settings, {"rootOrgId": "root-1"}) is True

    def test_organisation_match_via_ministry_or_state_id(self):
        settings = {"organisation": ["ministry-1"]}
        ctx = {"rootOrgId": "root-1", "ministry_or_state_id": "ministry-1"}
        assert _check_secure_settings_eligibility(settings, ctx) is True

    def test_organisation_mismatch(self):
        settings = {"organisation": ["some-other-org"]}
        ctx = {"rootOrgId": "root-1", "ministry_or_state_id": "ministry-1"}
        assert _check_secure_settings_eligibility(settings, ctx) is False

    def test_verified_required_and_profile_verified(self):
        settings = {"isVerifiedKarmayogi": "Yes"}
        assert _check_secure_settings_eligibility(settings, {"profile_status": "VERIFIED"}) is True

    def test_verified_required_case_insensitive(self):
        settings = {"isVerifiedKarmayogi": "yes"}
        assert _check_secure_settings_eligibility(settings, {"profile_status": "VERIFIED"}) is True

    def test_verified_required_and_profile_not_verified(self):
        settings = {"isVerifiedKarmayogi": "Yes"}
        assert _check_secure_settings_eligibility(settings, {"profile_status": "UNVERIFIED"}) is False


# ── get_user_eligibility_profile ─────────────────────────────────────────────

class TestGetUserEligibilityProfile:
    @patch("app.core.tools.enrolment_tools.requests.get")
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_success(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-1"}])
        mock_get.return_value = _profile_response()

        result = json.loads(get_user_eligibility_profile.func("user@x.com"))

        assert result["found"] is True
        assert result["first_name"] == "Asha"
        assert result["eligibility_ctx"]["group"] == ["Group A"]
        assert result["eligibility_ctx"]["rootOrgId"] == "root-1"
        assert result["eligibility_ctx"]["batch"] == "2015"

    @patch("app.core.tools.enrolment_tools.requests.get")
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_user_not_found(self, mock_post, mock_get):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_eligibility_profile.func("nobody@x.com"))

        assert result["found"] is False
        assert not mock_get.called

    @patch("app.core.tools.enrolment_tools.requests.get")
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_profile_read_empty(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-1"}])
        mock_get.return_value = _mock_response({"result": {"response": {}}})

        result = json.loads(get_user_eligibility_profile.func("user@x.com"))

        assert result["found"] is False
        assert result["message"] == USER_PROFILE_NOT_FOUND_MESSAGE

    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_eligibility_profile.func("user@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── search_course_or_program ─────────────────────────────────────────────────

class TestSearchCourseOrProgram:
    @patch("app.core.tools.enrolment_tools.requests.get")
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_found_non_moderated(self, mock_post, mock_get):
        course_search = _mock_response({
            "result": {
                "content": [{"identifier": "c1", "name": "Intro Course", "status": "Live"}],
                "count": 1,
            }
        })
        mock_post.side_effect = [_search_response([{"id": "user-1"}]), course_search]
        mock_get.return_value = _profile_response()

        result = json.loads(search_course_or_program.func("Intro Course", "user@x.com"))

        assert result["found"] is True
        assert result["is_moderated"] is False
        assert "metadata_eligible" not in result
        assert result["course_id"] == "c1"

    @patch("app.core.tools.enrolment_tools.requests.get")
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_found_moderated_eligible(self, mock_post, mock_get):
        course_search = _mock_response({
            "result": {
                "content": [{
                    "identifier": "c2",
                    "name": "Moderated Course",
                    "status": "Live",
                    "secureSettings": {"organisation": ["root-1"]},
                }],
                "count": 1,
            }
        })
        mock_post.side_effect = [_search_response([{"id": "user-1"}]), course_search]
        mock_get.return_value = _profile_response()

        result = json.loads(search_course_or_program.func("Moderated Course", "user@x.com"))

        assert result["is_moderated"] is True
        assert result["metadata_eligible"] is True

    @patch("app.core.tools.enrolment_tools.requests.get")
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_found_moderated_ineligible(self, mock_post, mock_get):
        course_search = _mock_response({
            "result": {
                "content": [{
                    "identifier": "c3",
                    "name": "Moderated Course",
                    "status": "Live",
                    "secureSettings": {"organisation": ["some-other-org"]},
                }],
                "count": 1,
            }
        })
        mock_post.side_effect = [_search_response([{"id": "user-1"}]), course_search]
        mock_get.return_value = _profile_response()

        result = json.loads(search_course_or_program.func("Moderated Course", "user@x.com"))

        assert result["is_moderated"] is True
        assert result["metadata_eligible"] is False

    @patch("app.core.tools.enrolment_tools.requests.get")
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_not_found(self, mock_post, mock_get):
        course_search = _mock_response({"result": {"content": [], "count": 0}})
        mock_post.side_effect = [_search_response([{"id": "user-1"}]), course_search]
        mock_get.return_value = _profile_response()

        result = json.loads(search_course_or_program.func("Nonexistent Course", "user@x.com"))

        assert result["found"] is False
        assert result["count"] == 0

    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_eligibility_resolution_failure_still_returns_search_result(self, mock_post):
        course_search = _mock_response({
            "result": {
                "content": [{"identifier": "c1", "name": "Intro Course", "status": "Live"}],
                "count": 1,
            }
        })
        mock_post.side_effect = [Exception("timeout"), course_search]

        result = json.loads(search_course_or_program.func("Intro Course", "user@x.com"))

        assert result["found"] is True
        assert result["course_id"] == "c1"

    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_exception_in_search_is_handled(self, mock_post):
        mock_post.side_effect = [_search_response([]), Exception("boom")]

        result = json.loads(search_course_or_program.func("Intro Course", "user@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── search_event ──────────────────────────────────────────────────────────────

class TestSearchEvent:
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_found(self, mock_post):
        mock_post.return_value = _mock_response({
            "result": {
                "Event": [{"identifier": "e1", "name": "Town Hall", "status": "Live"}],
                "count": 1,
            }
        })

        result = json.loads(search_event.func("Town Hall", "user@x.com"))

        assert result["found"] is True
        assert result["event_id"] == "e1"

    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_not_found(self, mock_post):
        mock_post.return_value = _mock_response({"result": {"Event": [], "count": 0}})

        result = json.loads(search_event.func("Nonexistent Event", "user@x.com"))

        assert result["found"] is False

    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(search_event.func("Town Hall", "user@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── check_course_or_event_access ─────────────────────────────────────────────

class TestCheckCourseOrEventAccess:
    @patch("app.core.tools.enrolment_tools.requests.get")
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_no_access_config_returns_eligible(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-1"}])
        mock_get.side_effect = [_profile_response(), _mock_response({}, status_code=404)]

        result = json.loads(check_course_or_event_access.func("c1", "user@x.com"))

        assert result["eligible"] is True

    @patch("app.core.tools.enrolment_tools.requests.get")
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_eligible_via_matching_user_group(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-1"}])
        access_settings = _mock_response({
            "result": {
                "accessControl": {
                    "userGroups": [{
                        "userGroupCriteriaList": [{"criteriaKey": "group", "criteriaValue": ["Group A"]}],
                    }]
                }
            }
        })
        mock_get.side_effect = [_profile_response(), access_settings]

        result = json.loads(check_course_or_event_access.func("c1", "user@x.com"))

        assert result["eligible"] is True

    @patch("app.core.tools.enrolment_tools.requests.get")
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_not_eligible_when_no_group_matches(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-1"}])
        access_settings = _mock_response({
            "result": {
                "accessControl": {
                    "userGroups": [{
                        "userGroupCriteriaList": [{"criteriaKey": "group", "criteriaValue": ["Group Z"]}],
                    }]
                }
            }
        })
        mock_get.side_effect = [_profile_response(), access_settings]

        result = json.loads(check_course_or_event_access.func("c1", "user@x.com"))

        assert result["eligible"] is False

    @patch("app.core.tools.enrolment_tools.requests.get")
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_user_not_found_still_checks_access(self, mock_post, mock_get):
        mock_post.return_value = _search_response([])
        mock_get.return_value = _mock_response({"result": {"accessControl": {}}})

        result = json.loads(check_course_or_event_access.func("c1", "user@x.com"))

        assert result["eligible"] is True
        assert mock_get.call_count == 1

    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(check_course_or_event_access.func("c1", "user@x.com"))

        assert "eligible" not in result
        assert "error" in result


# ── get_mdo_admin ─────────────────────────────────────────────────────────────

class TestGetMdoAdmin:
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_success(self, mock_post):
        admin = {
            "organisations": [{"roles": ["MDO_LEADER"]}],
            "profileDetails": {
                "personalDetails": {
                    "firstname": "Lead",
                    "primaryEmail": "lead@x.com",
                    "mobile": "9876543210",
                }
            },
        }
        mock_post.side_effect = [
            _search_response([{"id": "user-1", "rootOrgId": "root-1"}]),
            _search_response([admin]),
        ]

        result = json.loads(get_mdo_admin.func("user@x.com"))

        assert result["found"] is True
        assert result["_spoc_replacements"]["{{MDO_ADMIN_NAME}}"] == "Lead"
        assert result["_spoc_replacements"]["{{MDO_ADMIN_EMAIL}}"] == "lead@x.com"

    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_mdo_admin.func("nobody@x.com"))

        assert result["found"] is False
        assert result["message"] == USER_PROFILE_NOT_FOUND_MESSAGE

    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_root_org_id_missing(self, mock_post):
        mock_post.return_value = _search_response([{"id": "user-1"}])

        result = json.loads(get_mdo_admin.func("user@x.com"))

        assert result["found"] is False
        assert "rootOrgId" in result["message"]

    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_no_admin_found(self, mock_post):
        mock_post.side_effect = [
            _search_response([{"id": "user-1", "rootOrgId": "root-1", "rootOrgName": "Org X"}]),
            _search_response([]),
            _search_response([]),
        ]

        result = json.loads(get_mdo_admin.func("user@x.com"))

        assert result["found"] is False
        assert result["ministry_or_state_hint"] == "Org X"

    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_mdo_admin.func("user@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── get_yp_am_contact ─────────────────────────────────────────────────────────

class TestGetYpAmContact:
    @patch("app.core.tools.enrolment_tools.lookup_yp_by_mdo")
    def test_found(self, mock_lookup):
        mock_lookup.return_value = [{
            "centre_state": "Centre",
            "mdo": "Department of Atomic Energy",
            "spoc": "Akshay",
            "email": "akshay@x.com",
            "mobile": "9910210521",
            "yp_email": "yp@x.com",
        }]

        result = json.loads(get_yp_am_contact.func("atomic energy"))

        assert result["found"] is True
        assert result["count"] == 1
        assert result["_spoc_replacements"]["{{YP_AM_NAME}}"] == "Akshay"

    @patch("app.core.tools.enrolment_tools.lookup_yp_by_mdo")
    def test_not_found(self, mock_lookup):
        mock_lookup.return_value = []

        result = json.loads(get_yp_am_contact.func("Unknown Org"))

        assert result["found"] is False

    @patch("app.core.tools.enrolment_tools.lookup_yp_by_mdo")
    def test_exception_is_handled(self, mock_lookup):
        mock_lookup.side_effect = Exception("csv read error")

        result = json.loads(get_yp_am_contact.func("Some Org"))

        assert result["found"] is False
        assert "error" in result


# ── Convenience list ─────────────────────────────────────────────────────────

class TestGetEnrolmentTools:
    def test_returns_expected_tools(self):
        tools = get_enrolment_tools()
        names = {t.name for t in tools}

        assert names == {
            "get_user_eligibility_profile",
            "search_course_or_program",
            "search_event",
            "check_course_or_event_access",
            "get_mdo_admin",
            "get_yp_am_contact",
        }
