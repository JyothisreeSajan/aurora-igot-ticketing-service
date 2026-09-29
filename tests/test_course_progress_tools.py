"""
Unit tests for app/core/tools/course_progress_tools.py — tools used by
ContentRelatedSubgraph's "Course / Program Progress Issue" flow
(content_related_issue -> course_program_progress_issue: progress not
updating / certificate not generated, for courses/programs).
"""

import json
from unittest.mock import MagicMock, patch

from app.core.tools.course_progress_tools import (
    USER_NOT_FOUND_MESSAGE,
    _as_int,
    _detect_technical_issue,
    _extract_batch_id,
    _flatten_lang_content_status,
    _has_issued_certificate,
    diagnose_course_progress,
    get_assessment_remaining_attempts,
    get_course_progress_tools,
    get_incomplete_resource_details,
    get_user_course_enrollments,
)


def _mock_response(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _user_search_response(content):
    return _mock_response({"result": {"response": {"content": content}}})


def _enrollment_response(courses):
    return _mock_response({"result": {"courses": courses}})


def _content_response(content):
    return _mock_response({"result": {"content": content}})


# ── _extract_batch_id ─────────────────────────────────────────────────────────

class TestExtractBatchId:
    def test_top_level_batch_id(self):
        assert _extract_batch_id({"batchId": "b1"}) == "b1"

    def test_none_sentinel_falls_back(self):
        assert _extract_batch_id({"batchId": "NONE", "batches": [{"batchId": "b2"}]}) == "b2"

    def test_missing_falls_back_to_batches(self):
        assert _extract_batch_id({"batches": [{"batchId": "b3"}]}) == "b3"

    def test_no_batch_anywhere(self):
        assert _extract_batch_id({}) is None


# ── _has_issued_certificate ─────────────────────────────────────────────────────

class TestHasIssuedCertificate:
    def test_true_when_non_empty(self):
        assert _has_issued_certificate({"issuedCertificates": [{"id": "cert1"}]}) is True

    def test_false_when_empty_list(self):
        assert _has_issued_certificate({"issuedCertificates": []}) is False

    def test_false_when_missing(self):
        assert _has_issued_certificate({}) is False


# ── _as_int ──────────────────────────────────────────────────────────────────

class TestAsInt:
    def test_int_passthrough(self):
        assert _as_int(2) == 2

    def test_numeric_string(self):
        assert _as_int("2") == 2

    def test_invalid_uses_default(self):
        assert _as_int("not-a-number", default=-1) == -1

    def test_none_uses_default(self):
        assert _as_int(None) == 0


# ── _flatten_lang_content_status ────────────────────────────────────────────────

class TestFlattenLangContentStatus:
    def test_single_language(self):
        status = {"en": {"r1": 2, "r2": 0}}
        assert _flatten_lang_content_status(status) == {"r1": 2, "r2": 0}

    def test_takes_max_across_languages(self):
        status = {"en": {"r1": 1}, "hi": {"r1": 2}}
        assert _flatten_lang_content_status(status) == {"r1": 2}

    def test_empty_input(self):
        assert _flatten_lang_content_status({}) == {}

    def test_none_input(self):
        assert _flatten_lang_content_status(None) == {}


# ── _detect_technical_issue ─────────────────────────────────────────────────────

class TestDetectTechnicalIssue:
    def test_mismatch_detected(self):
        lang_status = {"en": {"r1": 1}}
        admin_records = [{"contentid": "r1", "status": 2}]
        assert _detect_technical_issue(lang_status, admin_records) is True

    def test_no_mismatch_when_statuses_agree(self):
        lang_status = {"en": {"r1": 2}}
        admin_records = [{"contentid": "r1", "status": 2}]
        assert _detect_technical_issue(lang_status, admin_records) is False

    def test_no_mismatch_when_both_incomplete(self):
        lang_status = {"en": {"r1": 1}}
        admin_records = [{"contentid": "r1", "status": 1}]
        assert _detect_technical_issue(lang_status, admin_records) is False

    def test_unmatched_resource_ignored(self):
        lang_status = {"en": {"r1": 1}}
        admin_records = [{"contentid": "r-other", "status": 2}]
        assert _detect_technical_issue(lang_status, admin_records) is False

    def test_empty_records(self):
        assert _detect_technical_issue({"en": {"r1": 1}}, []) is False


# ── get_user_course_enrollments ─────────────────────────────────────────────────

class TestGetUserCourseEnrollments:
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_success(self, mock_post):
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([
                {"courseId": "c1", "courseName": "Leadership 101", "completionPercentage": 60,
                 "primaryCategory": "Course", "issuedCertificates": [], "batchId": "b1"},
            ]),
        ]

        result = json.loads(get_user_course_enrollments.func("user@x.com"))

        assert result["found"] is True
        assert result["count"] == 1
        assert result["courses"][0] == {
            "course_id": "c1",
            "course_name": "Leadership 101",
            "completion_pct": 60,
            "primary_category": "Course",
            "certificate_issued": False,
            "enrolled": True,
        }

    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _user_search_response([])

        result = json.loads(get_user_course_enrollments.func("nobody@x.com"))

        assert result["found"] is False
        assert result["message"] == USER_NOT_FOUND_MESSAGE

    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_no_enrollments(self, mock_post):
        mock_post.side_effect = [_user_search_response([{"id": "user-1"}]), _enrollment_response([])]

        result = json.loads(get_user_course_enrollments.func("user@x.com"))

        assert result["found"] is False

    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_course_enrollments.func("user@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── diagnose_course_progress ────────────────────────────────────────────────────

class TestDiagnoseCourseProgress:
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_certificate_already_issued(self, mock_post):
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([
                {"courseId": "c1", "courseName": "Leadership 101",
                 "issuedCertificates": [{"id": "cert1"}], "batchId": "b1"},
            ]),
        ]

        result = json.loads(diagnose_course_progress.func("c1", "user@x.com"))

        assert result["status"] == "certificate_issued"
        assert result["course_name"] == "Leadership 101"

    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_no_enrollment_record(self, mock_post):
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([]),
        ]

        result = json.loads(diagnose_course_progress.func("c1", "user@x.com"))

        assert result["status"] == "not_enrolled"

    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_no_batch_id_is_not_enrolled(self, mock_post):
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([
                {"courseId": "c1", "courseName": "Leadership 101", "issuedCertificates": []},
            ]),
        ]

        result = json.loads(diagnose_course_progress.func("c1", "user@x.com"))

        assert result["status"] == "not_enrolled"
        assert result["course_name"] == "Leadership 101"

    @patch("app.core.tools.course_progress_tools.requests.get")
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_technical_issue_detected(self, mock_post, mock_get):
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([
                {"courseId": "c1", "courseName": "Leadership 101", "issuedCertificates": [],
                 "batchId": "b1", "completionPercentage": 50,
                 "langContentStatus": {"en": {"r1": 1}}},
            ]),
            _mock_response({"consumptionRecords": [{"contentid": "r1", "status": 2}]}),
        ]
        mock_get.return_value = _content_response({"primaryCategory": "Course", "leafNodes": ["r1"]})

        result = json.loads(diagnose_course_progress.func("c1", "user@x.com"))

        assert result["status"] == "technical_issue"
        assert result["course_name"] == "Leadership 101"

    @patch("app.core.tools.course_progress_tools.requests.get")
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_completion_100_needs_revalidation(self, mock_post, mock_get):
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([
                {"courseId": "c1", "courseName": "Leadership 101", "issuedCertificates": [],
                 "batchId": "b1", "completionPercentage": 100,
                 "langContentStatus": {"en": {"r1": 2}}},
            ]),
            _mock_response({"consumptionRecords": [{"contentid": "r1", "status": 2}]}),
        ]
        mock_get.return_value = _content_response({"primaryCategory": "Course", "leafNodes": ["r1"]})

        result = json.loads(diagnose_course_progress.func("c1", "user@x.com"))

        assert result["status"] == "needs_revalidation"

    @patch("app.core.tools.course_progress_tools.requests.get")
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_revalidation_still_stuck_escalates(self, mock_post, mock_get):
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([
                {"courseId": "c1", "courseName": "Leadership 101", "issuedCertificates": [],
                 "batchId": "b1", "completionPercentage": 100,
                 "langContentStatus": {"en": {"r1": 2}}},
            ]),
            _mock_response({"consumptionRecords": [{"contentid": "r1", "status": 2}]}),
        ]
        mock_get.return_value = _content_response({"primaryCategory": "Course", "leafNodes": ["r1"]})

        result = json.loads(diagnose_course_progress.func("c1", "user@x.com", True))

        assert result["status"] == "certificate_generation_failure"

    @patch("app.core.tools.course_progress_tools.requests.get")
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_resources_pending_via_leaf_diff(self, mock_post, mock_get):
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([
                {"courseId": "c1", "courseName": "Leadership 101", "issuedCertificates": [],
                 "batchId": "b1", "completionPercentage": 40,
                 "langContentStatus": {"en": {"r1": 2}}},
            ]),
            _mock_response({"consumptionRecords": [{"contentid": "r1", "status": 2}]}),
            _enrollment_response([
                {"courseId": "c1", "langContentStatus": {"en": {"r1": 2}}},
            ]),
        ]
        mock_get.return_value = _content_response(
            {"primaryCategory": "Course", "leafNodes": ["r1", "r2", "r3"]}
        )

        result = json.loads(diagnose_course_progress.func("c1", "user@x.com"))

        assert result["status"] == "resources_pending"
        assert sorted(result["incomplete_ids"]) == ["r2", "r3"]

    @patch("app.core.tools.course_progress_tools.requests.get")
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_unrelated_enrollment_completion_not_counted(self, mock_post, mock_get):
        """A leaf resource ID completed in some OTHER, unrelated course must not
        be treated as complete here — the cross-enrollment scan is scoped to
        this course/program's own child_course_ids, not every enrollment the
        user has anywhere."""
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([
                {"courseId": "c1", "courseName": "Leadership 101", "issuedCertificates": [],
                 "batchId": "b1", "completionPercentage": 40,
                 "langContentStatus": {"en": {"r1": 1}}},
            ]),
            _mock_response({"consumptionRecords": [{"contentid": "r1", "status": 1}]}),
            _enrollment_response([
                {"courseId": "c1", "langContentStatus": {"en": {"r1": 1}}},
                {"courseId": "unrelated-course", "langContentStatus": {"en": {"r1": 2}}},
            ]),
        ]
        mock_get.return_value = _content_response(
            {"primaryCategory": "Course", "leafNodes": ["r1", "r2"]}
        )

        result = json.loads(diagnose_course_progress.func("c1", "user@x.com"))

        assert result["status"] == "resources_pending"
        assert sorted(result["incomplete_ids"]) == ["r1", "r2"]

    @patch("app.core.tools.course_progress_tools.requests.get")
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_program_uses_hierarchy_child_courses(self, mock_post, mock_get):
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([
                {"courseId": "p1", "courseName": "Leadership Program", "issuedCertificates": [],
                 "batchId": "b1", "completionPercentage": 50,
                 "langContentStatus": {"en": {"r1": 1}}},
            ]),
            _mock_response({"consumptionRecords": [{"contentid": "r1", "status": 1}]}),
            _enrollment_response([{"courseId": "p1", "langContentStatus": {}}]),
        ]
        mock_get.side_effect = [
            _content_response({"primaryCategory": "Program", "leafNodes": ["r1"]}),
            _mock_response({"result": {"content": {"children": [{"identifier": "child1"}]}}}),
        ]

        result = json.loads(diagnose_course_progress.func("p1", "user@x.com"))

        assert result["status"] == "resources_pending"
        # admin content state was called with the child course id, not the program id
        admin_state_call = mock_post.call_args_list[2]
        assert admin_state_call.kwargs["json"]["request"]["courseId"] == "child1"

    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _user_search_response([])

        result = json.loads(diagnose_course_progress.func("c1", "nobody@x.com"))

        assert result["status"] == "error"

    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(diagnose_course_progress.func("c1", "user@x.com"))

        assert result["status"] == "error"


# ── get_incomplete_resource_details ─────────────────────────────────────────────

class TestGetIncompleteResourceDetails:
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_all_assessment(self, mock_post):
        mock_post.return_value = _mock_response({
            "result": {"content": [
                {"identifier": "a1", "name": "Final Assessment",
                 "primaryCategory": "Course Assessment", "mimeType": "application/vnd.sunbird.questionset",
                 "maxAttempts": 3, "maxAssessmentRetakeAttempts": 2},
            ]}
        })

        result = json.loads(get_incomplete_resource_details.func(["a1"], "user@x.com"))

        assert result["all_resources_assessment"] is True
        assert result["has_scorm_resources"] is False
        assert result["assessment_id"] == "a1"
        assert result["max_attempts"] == 3
        assert result["max_retake_attempts"] == 2

    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_scorm_resource(self, mock_post):
        mock_post.return_value = _mock_response({
            "result": {"content": [
                {"identifier": "s1", "name": "SCORM Module", "primaryCategory": "Learning Resource",
                 "mimeType": "application/vnd.ekstep.html-archive", "duration": "600"},
            ]}
        })

        result = json.loads(get_incomplete_resource_details.func(["s1"], "user@x.com"))

        assert result["has_scorm_resources"] is True
        assert result["all_resources_assessment"] is False
        assert result["scorm_resource_name"] == "SCORM Module"
        assert result["scorm_resource_duration_min"] == 10.0

    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_standard_resource(self, mock_post):
        mock_post.return_value = _mock_response({
            "result": {"content": [
                {"identifier": "v1", "name": "Intro Video", "primaryCategory": "Learning Resource",
                 "mimeType": "video/mp4"},
            ]}
        })

        result = json.loads(get_incomplete_resource_details.func(["v1"], "user@x.com"))

        assert result["has_scorm_resources"] is False
        assert result["all_resources_assessment"] is False
        assert result["resource_names"] == ["Intro Video"]

    @patch("app.core.tools.course_progress_tools.requests.get")
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_missing_id_falls_back_to_content_read(self, mock_post, mock_get):
        mock_post.return_value = _mock_response({"result": {"content": []}})
        mock_get.return_value = _content_response(
            {"identifier": "v1", "name": "Intro Video", "primaryCategory": "Learning Resource", "mimeType": "video/mp4"}
        )

        result = json.loads(get_incomplete_resource_details.func(["v1"], "user@x.com"))

        assert result["resource_names"] == ["Intro Video"]

    @patch("app.core.tools.course_progress_tools.requests.get")
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_nothing_found_returns_error(self, mock_post, mock_get):
        mock_post.return_value = _mock_response({"result": {"content": []}})
        mock_get.side_effect = Exception("not found")

        result = json.loads(get_incomplete_resource_details.func(["v1"], "user@x.com"))

        assert "error" in result

    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_incomplete_resource_details.func(["v1"], "user@x.com"))

        assert "error" in result


# ── get_assessment_remaining_attempts ───────────────────────────────────────────

class TestGetAssessmentRemainingAttempts:
    @patch("app.core.tools.course_progress_tools.requests.get")
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_attempts_remaining(self, mock_post, mock_get):
        mock_post.return_value = _user_search_response([{"id": "user-1"}])
        mock_get.return_value = _mock_response({"attemptsAllowed": 3, "attemptsMade": 1})

        result = json.loads(get_assessment_remaining_attempts.func("a1", "user@x.com"))

        assert result["remaining_attempts"] == 2
        assert result["max_attempts"] == 3
        assert result["used_attempts"] == 1

    @patch("app.core.tools.course_progress_tools.requests.get")
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_attempts_exhausted(self, mock_post, mock_get):
        mock_post.return_value = _user_search_response([{"id": "user-1"}])
        mock_get.return_value = _mock_response({"attemptsAllowed": 3, "attemptsMade": 3})

        result = json.loads(get_assessment_remaining_attempts.func("a1", "user@x.com"))

        assert result["remaining_attempts"] == 0

    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _user_search_response([])

        result = json.loads(get_assessment_remaining_attempts.func("a1", "nobody@x.com"))

        assert result["error"] == USER_NOT_FOUND_MESSAGE

    @patch("app.core.tools.course_progress_tools.requests.get")
    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_missing_attempt_fields(self, mock_post, mock_get):
        mock_post.return_value = _user_search_response([{"id": "user-1"}])
        mock_get.return_value = _mock_response({})

        result = json.loads(get_assessment_remaining_attempts.func("a1", "user@x.com"))

        assert "error" in result

    @patch("app.core.tools.course_progress_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_assessment_remaining_attempts.func("a1", "user@x.com"))

        assert "error" in result


# ── get_course_progress_tools ───────────────────────────────────────────────────

class TestGetCourseProgressTools:
    def test_returns_all_four_tools(self):
        tools = get_course_progress_tools()
        assert {t.name for t in tools} == {
            "get_user_course_enrollments",
            "diagnose_course_progress",
            "get_incomplete_resource_details",
            "get_assessment_remaining_attempts",
        }
