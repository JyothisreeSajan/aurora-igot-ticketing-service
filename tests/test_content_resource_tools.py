"""
Unit tests for app/core/tools/content_resource_tools.py — tools used by
ContentRelatedSubgraph's "Content / Resource Not Opening" web-browser flow
(content_related_issue -> content_resource_not_opening): course/resource
identification, resource-type resolution (incl. composite-search fallback),
and the YouTube streamingUrl/artifactUrl/previewUrl configuration check.
"""

import json
from unittest.mock import MagicMock, patch

from app.core.tools.content_resource_tools import (
    USER_NOT_FOUND_MESSAGE,
    check_youtube_resource_urls,
    get_content_resource_tools,
    get_course_resources,
    get_user_course_enrollments,
)


def _mock_response(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _user_search_response(content):
    return _mock_response({"result": {"response": {"content": content}}})


def _enrollment_response(courses):
    return _mock_response({"result": {"courses": courses}})


def _hierarchy_response(content):
    return _mock_response({"result": {"content": content}})


def _composite_response(content):
    return _mock_response({"result": {"content": content}})


# ── get_user_course_enrollments ─────────────────────────────────────────────────

class TestGetUserCourseEnrollments:
    @patch("app.core.tools.content_resource_tools.requests.post")
    def test_found_returns_courses(self, mock_post):
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([
                {"courseId": "c1", "courseName": "Leadership 101", "completionPercentage": 60},
            ]),
        ]

        result = json.loads(get_user_course_enrollments.func("user@x.com"))

        assert result["found"] is True
        assert result["count"] == 1
        assert result["courses"][0] == {
            "course_id": "c1", "course_name": "Leadership 101", "completion_pct": 60,
        }

    @patch("app.core.tools.content_resource_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _user_search_response([])

        result = json.loads(get_user_course_enrollments.func("nobody@x.com"))

        assert result["found"] is False
        assert result["message"] == USER_NOT_FOUND_MESSAGE

    @patch("app.core.tools.content_resource_tools.requests.post")
    def test_no_enrollments(self, mock_post):
        mock_post.side_effect = [_user_search_response([{"id": "user-1"}]), _enrollment_response([])]

        result = json.loads(get_user_course_enrollments.func("user@x.com"))

        assert result["found"] is False

    @patch("app.core.tools.content_resource_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_course_enrollments.func("user@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── get_course_resources ─────────────────────────────────────────────────────────

class TestGetCourseResources:
    @patch("app.core.tools.content_resource_tools.requests.get")
    @patch("app.core.tools.content_resource_tools.requests.post")
    def test_success_via_leaf_nodes(self, mock_post, mock_get):
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([
                {"courseId": "c1", "courseName": "Leadership 101",
                 "langContentStatus": {"en": {"r1": 2}}},
            ]),
            _composite_response([
                {"identifier": "r1", "name": "Intro Video", "mimeType": "video/mp4"},
                {"identifier": "r2", "name": "Final Assessment", "mimeType": "application/vnd.sunbird.questionset"},
            ]),
        ]
        mock_get.return_value = _hierarchy_response({"leafNodes": ["r1", "r2"]})

        result = json.loads(get_course_resources.func("c1", "user@x.com"))

        assert result["found"] is True
        assert result["course_name"] == "Leadership 101"
        assert result["resources"] == [
            {"resource_id": "r1", "resource_name": "Intro Video", "mime_type": "video/mp4", "is_complete": True},
            {"resource_id": "r2", "resource_name": "Final Assessment", "mime_type": "application/vnd.sunbird.questionset", "is_complete": False},
        ]

    @patch("app.core.tools.content_resource_tools.requests.get")
    @patch("app.core.tools.content_resource_tools.requests.post")
    def test_falls_back_to_children_when_no_leaf_nodes(self, mock_post, mock_get):
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([{"courseId": "p1", "courseName": "Leadership Program", "langContentStatus": {}}]),
            _composite_response([{"identifier": "child1", "name": "Child Course", "mimeType": "application/vnd.ekstep.content-collection"}]),
        ]
        mock_get.return_value = _hierarchy_response({"leafNodes": [], "children": [{"identifier": "child1"}]})

        result = json.loads(get_course_resources.func("p1", "user@x.com"))

        assert result["found"] is True
        assert result["resources"][0]["resource_id"] == "child1"

    @patch("app.core.tools.content_resource_tools.requests.get")
    @patch("app.core.tools.content_resource_tools.requests.post")
    def test_composite_search_drops_resource_fallback_fills_it_in(self, mock_post, mock_get):
        """Real-world behavior: composite search can silently drop a resource
        (e.g. Draft/Retired status) — the per-id fallback read must fill it in."""
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([{"courseId": "c1", "courseName": "Course X", "langContentStatus": {}}]),
            _composite_response([{"identifier": "r1", "name": "Video", "mimeType": "video/mp4"}]),
        ]
        mock_get.side_effect = [
            _hierarchy_response({"leafNodes": ["r1", "r2"]}),
            _hierarchy_response({"name": "Dropped Assessment", "mimeType": "application/vnd.sunbird.questionset"}),
        ]

        result = json.loads(get_course_resources.func("c1", "user@x.com"))

        assert result["found"] is True
        names = {r["resource_name"] for r in result["resources"]}
        assert names == {"Video", "Dropped Assessment"}

    @patch("app.core.tools.content_resource_tools.requests.get")
    @patch("app.core.tools.content_resource_tools.requests.post")
    def test_no_resources_found_when_hierarchy_empty(self, mock_post, mock_get):
        mock_post.side_effect = [
            _user_search_response([{"id": "user-1"}]),
            _enrollment_response([{"courseId": "c1", "courseName": "Empty Course", "langContentStatus": {}}]),
        ]
        mock_get.return_value = _hierarchy_response({"leafNodes": [], "children": []})

        result = json.loads(get_course_resources.func("c1", "user@x.com"))

        assert result["found"] is False
        assert result["course_name"] == "Empty Course"

    @patch("app.core.tools.content_resource_tools.requests.post")
    def test_no_course_match(self, mock_post):
        mock_post.side_effect = [_user_search_response([{"id": "user-1"}]), _enrollment_response([])]

        result = json.loads(get_course_resources.func("c-missing", "user@x.com"))

        assert result["found"] is False
        assert result["message"] == "No enrollment record found for this course."

    @patch("app.core.tools.content_resource_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _user_search_response([])

        result = json.loads(get_course_resources.func("c1", "nobody@x.com"))

        assert result["found"] is False
        assert result["message"] == USER_NOT_FOUND_MESSAGE

    @patch("app.core.tools.content_resource_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_course_resources.func("c1", "user@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── check_youtube_resource_urls ──────────────────────────────────────────────────

class TestCheckYoutubeResourceUrls:
    @patch("app.core.tools.content_resource_tools.requests.get")
    def test_configured_when_all_three_urls_match(self, mock_get):
        mock_get.return_value = _mock_response({"result": {"content": {
            "streamingUrl": "https://youtube.com/embed/x",
            "artifactUrl": "https://youtube.com/embed/x",
            "previewUrl": "https://youtube.com/embed/x",
        }}})

        result = json.loads(check_youtube_resource_urls.func("r1", "user@x.com"))

        assert result["status"] == "configured"

    @patch("app.core.tools.content_resource_tools.requests.get")
    def test_misconfigured_when_urls_differ(self, mock_get):
        mock_get.return_value = _mock_response({"result": {"content": {
            "streamingUrl": "https://youtube.com/embed/x",
            "artifactUrl": "https://youtube.com/embed/y",
            "previewUrl": "https://youtube.com/embed/x",
        }}})

        result = json.loads(check_youtube_resource_urls.func("r1", "user@x.com"))

        assert result["status"] == "misconfigured"

    @patch("app.core.tools.content_resource_tools.requests.get")
    def test_misconfigured_when_streaming_url_missing(self, mock_get):
        mock_get.return_value = _mock_response({"result": {"content": {
            "streamingUrl": None,
            "artifactUrl": "https://youtube.com/embed/x",
            "previewUrl": None,
        }}})

        result = json.loads(check_youtube_resource_urls.func("r1", "user@x.com"))

        assert result["status"] == "misconfigured"

    @patch("app.core.tools.content_resource_tools.requests.get")
    def test_exception_is_handled(self, mock_get):
        mock_get.side_effect = Exception("timeout")

        result = json.loads(check_youtube_resource_urls.func("r1", "user@x.com"))

        assert result["status"] == "error"


# ── get_content_resource_tools ───────────────────────────────────────────────────

class TestGetContentResourceTools:
    def test_returns_all_three_tools(self):
        tools = get_content_resource_tools()
        assert {t.name for t in tools} == {
            "get_user_course_enrollments",
            "get_course_resources",
            "check_youtube_resource_urls",
        }
