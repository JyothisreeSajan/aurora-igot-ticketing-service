"""
Unit tests for app/core/tools/certificate_tools.py, covering two distinct
content_related_issue sub-categories:

  - get_user_details: "Certificate Issue" (Incorrect Name on Certificate) —
    the authoritative source of the name that appears on generated
    certificates.

  - get_user_enrollments / diagnose_certificate_receipt:
    "Certificate Not Received / Generated" (UC-03) — courses and programs;
    completion / issuedCertificates / 24h-timing / pending-resource +
    SCORM diagnosis.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.core.tools.certificate_tools import (
    _as_int,
    _flatten_lang_content_status,
    _hours_since_completion,
    _parse_completed_on,
    _resolve_pending_resources_metadata,
    diagnose_certificate_receipt,
    get_certificate_not_received_tools,
    get_user_details,
    get_user_enrollments,
)


def _mock_response(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _search_response(content):
    return _mock_response({"result": {"response": {"content": content}}})


class TestGetUserDetails:
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_found_returns_filtered_fields(self, mock_post):
        user = {
            "rootOrgName": "Ministry of Power",
            "channel": "Ministry of Power",
            "id": "user-1",
            "rootOrgId": "root-1",
            "firstName": "Bharath",
            "lastName": "Kumar",
            "profileDetails": {
                "professionalDetails": [
                    {"verifiedKarmayogi": True, "profileStatus": "VERIFIED", "designation": "Deputy Adviser"}
                ]
            },
            "userType": "OFFICIAL",
            "status": 1,
            "roles": ["PUBLIC"],
            "phoneVerified": True,
            "userName": "bharath_k",
            "emailVerified": True,
        }
        mock_post.return_value = _search_response([user])

        result = json.loads(get_user_details.func("bharath@x.com"))

        assert result["firstName"] == "Bharath"
        assert result["lastName"] == "Kumar"
        assert result["rootOrgName"] == "Ministry of Power"
        assert result["profileDetails"]["professionalDetails"][0]["designation"] == "Deputy Adviser"
        assert "error" not in result

    @patch("app.core.tools.certificate_tools.requests.post")
    def test_missing_professional_details_defaults_gracefully(self, mock_post):
        user = {"firstName": "Ravi", "lastName": None, "profileDetails": {}}
        mock_post.return_value = _search_response([user])

        result = json.loads(get_user_details.func("ravi@x.com"))

        assert result["firstName"] == "Ravi"
        assert result["lastName"] is None
        assert result["profileDetails"]["professionalDetails"][0]["designation"] is None

    @patch("app.core.tools.certificate_tools.requests.post")
    def test_not_found_returns_error(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_details.func("nobody@x.com"))

        assert result["error"] == "User details not found."

    @patch("app.core.tools.certificate_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_details.func("err@x.com"))

        assert "error" in result
        assert "firstName" not in result


# ── _as_int / _flatten_lang_content_status ──────────────────────────────────────

class TestAsInt:
    def test_int_passthrough(self):
        assert _as_int(2) == 2

    def test_numeric_string(self):
        assert _as_int("2") == 2

    def test_invalid_uses_default(self):
        assert _as_int("not-a-number", default=-1) == -1

    def test_none_uses_default(self):
        assert _as_int(None) == 0


class TestFlattenLangContentStatus:
    def test_takes_max_across_languages(self):
        status = {"en": {"r1": 1}, "hi": {"r1": 2}}
        assert _flatten_lang_content_status(status) == {"r1": 2}

    def test_empty_input(self):
        assert _flatten_lang_content_status({}) == {}

    def test_none_input(self):
        assert _flatten_lang_content_status(None) == {}


# ── _parse_completed_on / _hours_since_completion ───────────────────────────────

class TestParseCompletedOnAndHoursSince:
    def test_epoch_millis(self):
        millis = int((datetime.now(timezone.utc) - timedelta(hours=10)).timestamp() * 1000)
        hours = _hours_since_completion(millis)
        assert hours is not None
        assert 9.9 <= hours <= 10.1

    def test_epoch_seconds(self):
        seconds = (datetime.now(timezone.utc) - timedelta(hours=48)).timestamp()
        hours = _hours_since_completion(seconds)
        assert hours is not None
        assert 47.9 <= hours <= 48.1

    def test_iso_string(self):
        iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        hours = _hours_since_completion(iso)
        assert hours is not None
        assert 1.9 <= hours <= 2.1

    def test_none_returns_none(self):
        assert _parse_completed_on(None) is None
        assert _hours_since_completion(None) is None

    def test_unparsable_returns_none(self):
        assert _parse_completed_on("not-a-date") is None
        assert _hours_since_completion("not-a-date") is None


# ── get_user_enrollments ─────────────────────────────────────────────────────────

def _enrollment_list_response(courses):
    return _mock_response({"responseCode": "OK", "result": {"courses": courses}})


class TestGetUserEnrollments:
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_found_returns_filtered_courses(self, mock_post):
        mock_post.side_effect = [
            _search_response([{"id": "user-1"}]),
            _enrollment_list_response([
                {"courseId": "c1", "courseName": "Leadership 101", "status": 2,
                 "completionPercentage": 100, "completedOn": 123456, "issuedCertificates": [{"id": "x"}],
                 "enrolledDate": 999},
            ]),
        ]

        result = json.loads(get_user_enrollments.func("user@x.com"))

        assert result["result"]["courses"][0]["courseName"] == "Leadership 101"
        assert result["result"]["courses"][0]["status"] == 2
        assert "error" not in result

    @patch("app.core.tools.certificate_tools.requests.post")
    def test_null_enrolled_date_does_not_crash_sort(self, mock_post):
        """Regression: enrolledDate present but null must not break the sort
        (previously crashed with TypeError comparing None < int)."""
        mock_post.side_effect = [
            _search_response([{"id": "user-1"}]),
            _enrollment_list_response([
                {"courseId": "c1", "courseName": "No Enrolled Date", "status": 0, "enrolledDate": None},
                {"courseId": "c2", "courseName": "Has Enrolled Date", "status": 1, "enrolledDate": 500},
            ]),
        ]

        result = json.loads(get_user_enrollments.func("user@x.com"))

        assert "error" not in result
        assert result["result"]["courses"][0]["courseName"] == "Has Enrolled Date"

    @patch("app.core.tools.certificate_tools.requests.post")
    def test_status_filter_completed(self, mock_post):
        mock_post.side_effect = [
            _search_response([{"id": "user-1"}]),
            _enrollment_list_response([]),
        ]

        get_user_enrollments.func("user@x.com", "Completed")

        second_call_payload = mock_post.call_args_list[1].kwargs["json"]
        assert second_call_payload["request"]["status"] == ["Completed"]

    @patch("app.core.tools.certificate_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_enrollments.func("nobody@x.com"))

        assert result["error"] == "User not found."

    @patch("app.core.tools.certificate_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_enrollments.func("user@x.com"))

        assert "error" in result


# ── _resolve_pending_resources_metadata ─────────────────────────────────────────

class TestResolvePendingResourcesMetadata:
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_scorm_detected(self, mock_post):
        mock_post.return_value = _mock_response({
            "result": {"content": [
                {"identifier": "r1", "name": "SCORM Module", "mimeType": "application/vnd.ekstep.html-archive"},
                {"identifier": "r2", "name": "Video Resource", "mimeType": "video/mp4"},
            ]}
        })

        result = _resolve_pending_resources_metadata(["r1", "r2"])

        assert result["has_scorm_resources"] is True
        assert result["scorm_resource_name"] == "SCORM Module"
        assert set(result["pending_resource_names"]) == {"SCORM Module", "Video Resource"}

    @patch("app.core.tools.certificate_tools.requests.post")
    def test_no_scorm(self, mock_post):
        mock_post.return_value = _mock_response({
            "result": {"content": [{"identifier": "r1", "name": "Final Assessment", "mimeType": "application/vnd.sunbird.questionset"}]}
        })

        result = _resolve_pending_resources_metadata(["r1"])

        assert result["has_scorm_resources"] is False
        assert result["scorm_resource_name"] is None

    @patch("app.core.tools.certificate_tools.requests.get")
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_composite_search_failure_falls_back_per_id(self, mock_post, mock_get):
        mock_post.side_effect = Exception("composite search down")
        mock_get.return_value = _mock_response({"result": {"content": {"name": "Fallback Resource", "mimeType": "video/mp4"}}})

        result = _resolve_pending_resources_metadata(["r1"])

        assert result["pending_resource_names"] == ["Fallback Resource"]

    @patch("app.core.tools.certificate_tools.requests.get")
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_missing_id_falls_back_to_content_read(self, mock_post, mock_get):
        mock_post.return_value = _mock_response({"result": {"content": [{"identifier": "r1", "name": "Found via composite", "mimeType": "video/mp4"}]}})
        mock_get.return_value = _mock_response({"result": {"content": {"name": "Found via fallback", "mimeType": "video/mp4"}}})

        result = _resolve_pending_resources_metadata(["r1", "r2"])

        assert set(result["pending_resource_names"]) == {"Found via composite", "Found via fallback"}


# ── diagnose_certificate_receipt ─────────────────────────────────────────────────

class TestDiagnoseCertificateReceipt:
    @patch("app.core.tools.certificate_tools.requests.get")
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_not_enrolled(self, mock_post, mock_get):
        mock_post.side_effect = [_search_response([{"id": "user-1"}]), _enrollment_list_response([])]

        result = json.loads(diagnose_certificate_receipt.func("c1", "user@x.com"))

        assert result["status"] == "not_enrolled"

    @patch("app.core.tools.certificate_tools.requests.get")
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_not_started(self, mock_post, mock_get):
        mock_post.side_effect = [
            _search_response([{"id": "user-1"}]),
            _enrollment_list_response([{"courseId": "c1", "courseName": "New Course", "status": 0}]),
        ]
        mock_get.return_value = _mock_response({"result": {"content": {"primaryCategory": "Course", "leafNodes": []}}})

        result = json.loads(diagnose_certificate_receipt.func("c1", "user@x.com"))

        assert result["status"] == "not_started"
        assert result["primary_category"] == "Course"

    @patch("app.core.tools.certificate_tools.requests.get")
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_completed_certificate_already_issued_within_24h_still_available(self, mock_post, mock_get):
        """A cert issued even within the last 24h must go straight to the
        'download steps available' outcome, not the 'wait 24h' message."""
        completed_5h_ago = int((datetime.now(timezone.utc) - timedelta(hours=5)).timestamp() * 1000)
        mock_post.side_effect = [
            _search_response([{"id": "user-1"}]),
            _enrollment_list_response([{
                "courseId": "c1", "courseName": "Fast Course", "status": 2,
                "completedOn": completed_5h_ago, "issuedCertificates": [{"id": "x"}],
            }]),
        ]
        mock_get.return_value = _mock_response({"result": {"content": {"primaryCategory": "Course", "leafNodes": []}}})

        result = json.loads(diagnose_certificate_receipt.func("c1", "user@x.com"))

        assert result["status"] == "completed_over_24h"

    @patch("app.core.tools.certificate_tools.requests.get")
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_completed_no_cert_over_24h(self, mock_post, mock_get):
        completed_30h_ago = int((datetime.now(timezone.utc) - timedelta(hours=30)).timestamp() * 1000)
        mock_post.side_effect = [
            _search_response([{"id": "user-1"}]),
            _enrollment_list_response([{
                "courseId": "c1", "courseName": "Old Course", "status": 2,
                "completedOn": completed_30h_ago, "issuedCertificates": [],
            }]),
        ]
        mock_get.return_value = _mock_response({"result": {"content": {"primaryCategory": "Course", "leafNodes": []}}})

        result = json.loads(diagnose_certificate_receipt.func("c1", "user@x.com"))

        assert result["status"] == "completed_over_24h"

    @patch("app.core.tools.certificate_tools.requests.get")
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_completed_no_cert_missing_completed_on_treated_as_over_24h(self, mock_post, mock_get):
        mock_post.side_effect = [
            _search_response([{"id": "user-1"}]),
            _enrollment_list_response([{
                "courseId": "c1", "courseName": "No Timestamp Course", "status": 2,
                "completedOn": None, "issuedCertificates": [],
            }]),
        ]
        mock_get.return_value = _mock_response({"result": {"content": {"primaryCategory": "Course", "leafNodes": []}}})

        result = json.loads(diagnose_certificate_receipt.func("c1", "user@x.com"))

        assert result["status"] == "completed_over_24h"

    @patch("app.core.tools.certificate_tools.requests.get")
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_completed_no_cert_under_24h(self, mock_post, mock_get):
        completed_5h_ago = int((datetime.now(timezone.utc) - timedelta(hours=5)).timestamp() * 1000)
        mock_post.side_effect = [
            _search_response([{"id": "user-1"}]),
            _enrollment_list_response([{
                "courseId": "c1", "courseName": "Recent Course", "status": 2,
                "completedOn": completed_5h_ago, "issuedCertificates": [],
            }]),
        ]
        mock_get.return_value = _mock_response({"result": {"content": {"primaryCategory": "Course", "leafNodes": []}}})

        result = json.loads(diagnose_certificate_receipt.func("c1", "user@x.com"))

        assert result["status"] == "completed_under_24h"
        assert result["hours_remaining"] == 19.0

    @patch("app.core.tools.certificate_tools.requests.get")
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_in_progress_with_pending_resources(self, mock_post, mock_get):
        mock_post.side_effect = [
            _search_response([{"id": "user-1"}]),
            _enrollment_list_response([{
                "courseId": "c1", "courseName": "Partial Course", "status": 1,
                "langContentStatus": {"en": {"r1": 2}},
            }]),
            _mock_response({"result": {"content": [{"identifier": "r2", "name": "Module 2", "mimeType": "video/mp4"}]}}),
        ]
        mock_get.return_value = _mock_response({"result": {"content": {"primaryCategory": "Course", "leafNodes": ["r1", "r2"]}}})

        result = json.loads(diagnose_certificate_receipt.func("c1", "user@x.com"))

        assert result["status"] == "resources_pending"
        assert result["pending_resource_names"] == ["Module 2"]
        assert result["has_scorm_resources"] is False

    @patch("app.core.tools.certificate_tools.requests.get")
    @patch("app.core.tools.certificate_tools.requests.post")
    def test_in_progress_with_no_incomplete_ids_treated_as_available(self, mock_post, mock_get):
        """Sync/cache lag: portal says in-progress but every leaf node is
        already complete — treat as certificate available, not stuck-pending."""
        mock_post.side_effect = [
            _search_response([{"id": "user-1"}]),
            _enrollment_list_response([{
                "courseId": "c1", "courseName": "Laggy Course", "status": 1,
                "langContentStatus": {"en": {"r1": 2}},
            }]),
        ]
        mock_get.return_value = _mock_response({"result": {"content": {"primaryCategory": "Course", "leafNodes": ["r1"]}}})

        result = json.loads(diagnose_certificate_receipt.func("c1", "user@x.com"))

        assert result["status"] == "completed_over_24h"

    @patch("app.core.tools.certificate_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(diagnose_certificate_receipt.func("c1", "nobody@x.com"))

        assert result["status"] == "error"

    @patch("app.core.tools.certificate_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(diagnose_certificate_receipt.func("c1", "user@x.com"))

        assert result["status"] == "error"


# ── get_certificate_not_received_tools ──────────────────────────────────────────

class TestGetCertificateNotReceivedTools:
    def test_returns_both_tools(self):
        tools = get_certificate_not_received_tools()
        assert {t.name for t in tools} == {"get_user_enrollments", "diagnose_certificate_receipt"}
