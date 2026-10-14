"""
Unit tests for app/core/tools/enrolment_tools.py — tools used by
ContentRelatedSubgraph's "Enrolment Issues" flow (content_related_issue ->
enrolment_issues: unable to find/enroll in a course, program, moderated
course, or event).
"""

import json
from unittest.mock import MagicMock, patch

from app.core.utils.prompt_templates import (
    CLASSIFICATION_PROMPT,
    ENROLMENT_ISSUES_SYSTEM_PROMPT,
)
from app.core.tools.enrolment_tools import (
    USER_PROFILE_NOT_FOUND_MESSAGE,
    _check_secure_settings_eligibility,
    _check_user_eligibility,
    _criteria_group_matches,
    _parse_created_on,
    _select_best_content_match,
    check_course_or_event_access,
    get_enrolled_content_type,
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


# ── _parse_created_on ─────────────────────────────────────────────────────────

class TestParseCreatedOn:
    def test_valid_iso_string(self):
        from datetime import datetime, timezone

        assert _parse_created_on("2024-05-01T10:00:00+00:00") == datetime(2024, 5, 1, 10, 0, tzinfo=timezone.utc)

    def test_none_sorts_last(self):
        from datetime import datetime, timezone

        assert _parse_created_on(None) == datetime.min.replace(tzinfo=timezone.utc)

    def test_empty_string_sorts_last(self):
        from datetime import datetime, timezone

        assert _parse_created_on("") == datetime.min.replace(tzinfo=timezone.utc)

    def test_unparseable_string_sorts_last(self):
        from datetime import datetime, timezone

        assert _parse_created_on("not-a-date") == datetime.min.replace(tzinfo=timezone.utc)


# ── _select_best_content_match ───────────────────────────────────────────────

class TestSelectBestContentMatch:
    def test_empty_content_returns_none(self):
        assert _select_best_content_match([], "Intro Course") is None

    def test_single_result_returned(self):
        content = [{"identifier": "c1", "name": "Intro Course", "status": "Live"}]

        assert _select_best_content_match(content, "Intro Course")["identifier"] == "c1"

    def test_exact_title_match_preferred_over_partial(self):
        content = [
            {"identifier": "partial", "name": "Intro Course Advanced", "status": "Live",
             "createdOn": "2024-06-01T00:00:00+00:00"},
            {"identifier": "exact", "name": "Intro Course", "status": "Live",
             "createdOn": "2020-01-01T00:00:00+00:00"},
        ]

        result = _select_best_content_match(content, "Intro Course")

        assert result["identifier"] == "exact"

    def test_case_and_whitespace_insensitive_exact_match(self):
        content = [{"identifier": "c1", "name": "  Intro Course  ", "status": "Live"}]

        result = _select_best_content_match(content, " intro course ")

        assert result["identifier"] == "c1"

    def test_prefers_live_over_retired_among_exact_matches(self):
        content = [
            {"identifier": "retired-new", "name": "Intro Course", "status": "Retired",
             "createdOn": "2024-06-01T00:00:00+00:00"},
            {"identifier": "live-old", "name": "Intro Course", "status": "Live",
             "createdOn": "2020-01-01T00:00:00+00:00"},
        ]

        result = _select_best_content_match(content, "Intro Course")

        assert result["identifier"] == "live-old"

    def test_falls_back_to_most_recent_when_no_live_match(self):
        content = [
            {"identifier": "retired-old", "name": "Intro Course", "status": "Retired",
             "createdOn": "2020-01-01T00:00:00+00:00"},
            {"identifier": "retired-new", "name": "Intro Course", "status": "Retired",
             "createdOn": "2024-06-01T00:00:00+00:00"},
        ]

        result = _select_best_content_match(content, "Intro Course")

        assert result["identifier"] == "retired-new"

    def test_no_exact_match_falls_back_to_all_candidates(self):
        content = [{"identifier": "c1", "name": "Something Else Entirely", "status": "Live"}]

        result = _select_best_content_match(content, "Intro Course")

        assert result["identifier"] == "c1"

    def test_most_recent_among_multiple_live_matches(self):
        content = [
            {"identifier": "live-old", "name": "Intro Course", "status": "Live",
             "createdOn": "2020-01-01T00:00:00+00:00"},
            {"identifier": "live-new", "name": "Intro Course", "status": "Live",
             "createdOn": "2024-06-01T00:00:00+00:00"},
        ]

        result = _select_best_content_match(content, "Intro Course")

        assert result["identifier"] == "live-new"


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
    def test_found_returns_primary_category(self, mock_post, mock_get):
        course_search = _mock_response({
            "result": {
                "content": [{"identifier": "p1", "name": "Intro Program", "status": "Live",
                             "primaryCategory": "Program"}],
                "count": 1,
            }
        })
        mock_post.side_effect = [_search_response([{"id": "user-1"}]), course_search]
        mock_get.return_value = _profile_response()

        result = json.loads(search_course_or_program.func("Intro Program", "user@x.com"))

        assert result["found"] is True
        assert result["primary_category"] == "Program"

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

    @patch("app.core.tools.enrolment_tools.requests.get")
    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_picks_live_record_over_stale_retired_duplicate(self, mock_post, mock_get):
        """A stale Retired duplicate under the same title, created more recently
        than the real Live record, must not shadow the Live one."""
        course_search = _mock_response({
            "result": {
                "content": [
                    {"identifier": "retired-new", "name": "Intro Course", "status": "Retired",
                     "createdOn": "2024-06-01T00:00:00+00:00"},
                    {"identifier": "live-old", "name": "Intro Course", "status": "Live",
                     "createdOn": "2020-01-01T00:00:00+00:00"},
                ],
                "count": 2,
            }
        })
        mock_post.side_effect = [_search_response([{"id": "user-1"}]), course_search]
        mock_get.return_value = _profile_response()

        result = json.loads(search_course_or_program.func("Intro Course", "user@x.com"))

        assert result["found"] is True
        assert result["course_id"] == "live-old"
        assert result["status"] == "Live"


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

    @patch("app.core.tools.enrolment_tools.requests.post")
    def test_picks_exact_match_among_multiple_events(self, mock_post):
        mock_post.return_value = _mock_response({
            "result": {
                "Event": [
                    {"identifier": "e-other", "name": "Town Hall Follow-up", "status": "Live",
                     "createdOn": "2024-06-01T00:00:00+00:00"},
                    {"identifier": "e1", "name": "Town Hall", "status": "Live",
                     "createdOn": "2020-01-01T00:00:00+00:00"},
                ],
                "count": 2,
            }
        })

        result = json.loads(search_event.func("Town Hall", "user@x.com"))

        assert result["found"] is True
        assert result["event_id"] == "e1"


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
            "get_enrolled_content_type",
            "check_course_or_event_access",
            "get_mdo_admin",
            "get_yp_am_contact",
        }


# ── get_enrolled_content_type (ENROLMENT SOP 2 — unenroll) ──────────────────

def _enrollment(name, category, content_id="do_1"):
    return {"courseName": name, "contentId": content_id, "content": {"name": name, "primaryCategory": category}}


@patch("app.core.tools.enrolment_tools._fetch_enrollments")
@patch("app.core.tools.enrolment_tools._fetch_user_record")
class TestGetEnrolledContentType:
    def test_course_is_unenroll_supported(self, mock_user, mock_enrol):
        mock_user.return_value = {"id": "u1"}
        mock_enrol.return_value = [_enrollment("Intro Course", "Course", "do_9")]

        result = json.loads(get_enrolled_content_type.func("intro course", "u@x.com"))

        assert result["found"] is True
        assert result["primary_category"] == "Course"
        assert result["unenroll_supported"] is True
        assert result["content_id"] == "do_9"

    def test_non_course_categories_are_not_supported(self, mock_user, mock_enrol):
        mock_user.return_value = {"id": "u1"}
        for category in ("Program", "Curated Program", "Blended Program", "Standalone Assessment"):
            mock_enrol.return_value = [_enrollment("Some Content", category)]
            result = json.loads(get_enrolled_content_type.func("Some Content", "u@x.com"))
            assert result["primary_category"] == category
            assert result["unenroll_supported"] is False

    def test_unknown_category_gives_null_support_flag(self, mock_user, mock_enrol):
        mock_user.return_value = {"id": "u1"}
        mock_enrol.return_value = [{"courseName": "No Category", "contentId": "x", "content": {}}]

        result = json.loads(get_enrolled_content_type.func("No Category", "u@x.com"))

        assert result["found"] is True
        assert result["unenroll_supported"] is None

    def test_exact_match_beats_partial_and_partial_works_either_way(self, mock_user, mock_enrol):
        mock_user.return_value = {"id": "u1"}
        mock_enrol.return_value = [
            _enrollment("Python Basics Advanced", "Program"),
            _enrollment("Python Basics", "Course"),
        ]
        exact = json.loads(get_enrolled_content_type.func("python basics", "u@x.com"))
        assert exact["name"] == "Python Basics"

        mock_enrol.return_value = [_enrollment("Python Basics", "Course")]
        partial = json.loads(get_enrolled_content_type.func("my Python Basics course", "u@x.com"))
        assert partial["name"] == "Python Basics"

    def test_name_falls_back_to_content_name_when_course_name_missing(self, mock_user, mock_enrol):
        mock_user.return_value = {"id": "u1"}
        mock_enrol.return_value = [{"contentId": "c9", "content": {"name": "Fallback Course", "primaryCategory": "Course"}}]

        result = json.loads(get_enrolled_content_type.func("fallback course", "u@x.com"))

        assert result["found"] is True
        assert result["name"] == "Fallback Course"
        assert result["unenroll_supported"] is True

    def test_blank_course_name_finds_nothing(self, mock_user, mock_enrol):
        mock_user.return_value = {"id": "u1"}
        mock_enrol.return_value = [_enrollment("Intro Course", "Course")]

        assert json.loads(get_enrolled_content_type.func("   ", "u@x.com"))["found"] is False

    def test_no_match(self, mock_user, mock_enrol):
        mock_user.return_value = {"id": "u1"}
        mock_enrol.return_value = [_enrollment("Intro Course", "Course")]

        assert json.loads(get_enrolled_content_type.func("zzz", "u@x.com"))["found"] is False

    def test_user_not_found(self, mock_user, mock_enrol):
        mock_user.return_value = None

        result = json.loads(get_enrolled_content_type.func("Intro Course", "nobody@x.com"))

        assert result["found"] is False
        assert result["message"] == USER_PROFILE_NOT_FOUND_MESSAGE

    def test_api_error_is_handled(self, mock_user, mock_enrol):
        mock_user.side_effect = Exception("boom")

        result = json.loads(get_enrolled_content_type.func("Intro Course", "u@x.com"))

        assert result["found"] is False
        assert "error" in result


class TestUnenrollSopPrompt:
    def _sop(self):
        flat = " ".join(ENROLMENT_ISSUES_SYSTEM_PROMPT.split())
        return flat[flat.index("ENROLMENT SOP 2"):]

    def test_checks_primary_category_via_enrollment_tool(self):
        sop = self._sop()
        assert "get_enrolled_content_type" in sop
        assert 'exactly "Course"' in sop

    def test_course_reply_has_unenroll_steps(self):
        sop = self._sop()
        assert "the Unenroll option is now available on the iGOT Karmayogi portal" in sop
        assert "Click on the three dots visible on the course thumbnail." in sop
        assert "Select the Unenroll option." in sop

    def test_non_course_reply_wording(self):
        sop = self._sop()
        assert "the Unenroll option is available only for individual courses" in sop
        assert "program, event, moderated course, or assessment is not available at present" in sop

    def test_both_replies_when_content_unknown(self):
        assert "BOTH replies" in self._sop()

    def test_stated_type_decides_reply_when_no_name(self):
        sop = self._sop()
        assert "the type the user states decides" in sop
        assert "COURSE reply ONLY" in sop
        assert "NON-COURSE reply ONLY" in sop

    def test_not_enrolled_reply_when_named_content_not_found(self):
        sop = self._sop()
        assert "Do NOT call any other search tool" in sop
        assert "NOT ENROLLED reply" in sop
        assert "you are not currently enrolled in" in sop
        assert "the Unenroll option is not applicable" in sop
        assert "If the user says it is an event" in sop

    def test_combined_reply_is_one_continuous_message(self):
        sop = self._sop()
        assert "write ONE continuous reply, not two separate blocks" in sop
        assert "Please note that currently, the Unenroll option is available only" in sop
        assert "ONCE, at the very end" in sop

    def test_classification_routes_unenroll_to_enrolment_issues(self):
        flat = " ".join(CLASSIFICATION_PROMPT.split())
        assert "Requests to UNENROLL from, withdraw from, or leave" in flat
        assert 'sub_category="Enrolment Issues"' in flat

