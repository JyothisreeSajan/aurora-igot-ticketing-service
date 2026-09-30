"""
Unit tests for app/core/tools/event_tools.py — tools used by
ContentRelatedSubgraph's "Event Related Issue" flow (content_related_issue ->
event_related_issue: video missing / video not playing / progress not updating).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.core.tools import event_tools
from app.core.tools.event_tools import (
    USER_NOT_FOUND_MESSAGE,
    _extract_time_spent_seconds,
    _extract_youtube_video_id,
    _is_youtube_embed_url,
    _iso8601_duration_to_seconds,
    check_event_video_config,
    check_event_video_duration,
    diagnose_event_progress,
    get_event_tools,
    get_user_events,
)

VIDEO_ID = "abcdefghijk"
EMBED = f"https://www.youtube.com/embed/{VIDEO_ID}"


def _resp(payload):
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = payload
    return r


def _user(content):
    return _resp({"result": {"response": {"content": content}}})


def _events(events):
    return _resp({"result": {"events": events}})


def _event_item(event_id="do_1", name="Webinar", completion=50, time_spent=None, certs=None):
    item = {"contentId": event_id, "event": {"name": name}, "completionPercentage": completion,
            "issuedCertificates": certs or []}
    if time_spent is not None:
        item["lrcProgressDetails"] = json.dumps({"duration": time_spent})
    return item


def _run_progress(item):
    with patch.object(event_tools.requests, "post", return_value=_user([{"id": "u1"}])), \
         patch.object(event_tools.requests, "get", return_value=_events([item])):
        return json.loads(diagnose_event_progress.invoke({"event_id": "do_1", "email": "a@b.c"}))


class TestHelpers:
    @pytest.mark.parametrize("url,expected", [
        (EMBED, VIDEO_ID),
        (f"https://www.youtube.com/watch?v={VIDEO_ID}", VIDEO_ID),
        (f"https://youtu.be/{VIDEO_ID}", VIDEO_ID),
        ("https://example.com/video", None),
        (None, None),
    ])
    def test_extract_video_id(self, url, expected):
        assert _extract_youtube_video_id(url) == expected

    def test_embed_url(self):
        assert _is_youtube_embed_url(EMBED)
        assert not _is_youtube_embed_url(f"https://www.youtube.com/watch?v={VIDEO_ID}")
        assert not _is_youtube_embed_url(None)

    @pytest.mark.parametrize("value,expected", [
        ("PT45S", 45.0), ("PT1M", 60.0), ("PT1H2M3S", 3723.0), ("P0D", 0.0), ("bogus", None), (None, None),
    ])
    def test_iso_duration(self, value, expected):
        assert _iso8601_duration_to_seconds(value) == expected

    def test_time_spent_falls_back_to_user_event_consumption(self):
        item = {"lrcProgressDetails": "", "userEventConsumption": [{"progressdetails": '{"duration": 700}'}]}
        assert _extract_time_spent_seconds(item) == 700.0

    def test_time_spent_missing(self):
        assert _extract_time_spent_seconds({"lrcProgressDetails": "not json"}) is None


class TestGetUserEvents:
    def test_user_not_found(self):
        with patch.object(event_tools.requests, "post", return_value=_user([])):
            out = json.loads(get_user_events.invoke({"email": "a@b.c"}))
        assert out == {"found": False, "message": USER_NOT_FOUND_MESSAGE}

    def test_lists_events(self):
        with patch.object(event_tools.requests, "post", return_value=_user([{"id": "u1"}])), \
             patch.object(event_tools.requests, "get", return_value=_events([_event_item(certs=[{"x": 1}])])):
            out = json.loads(get_user_events.invoke({"email": "a@b.c"}))
        assert out["events"] == [{"event_id": "do_1", "event_name": "Webinar",
                                   "completion_pct": 50, "certificate_issued": True}]


class TestVideoDuration:
    def _run(self, link, yt_payload=None, yt_raises=False):
        event_resp = _resp({"result": {"event": {"registrationLink": link}}})
        yt_resp = _resp(yt_payload or {})

        def fake_get(url, **kw):
            if "youtube.test" in url:
                if yt_raises:
                    raise RuntimeError("quota")
                return yt_resp
            return event_resp

        with patch.object(event_tools, "GOOGLE_YOUTUBE_API_BASE_URL", "https://youtube.test"), \
             patch.object(event_tools, "GOOGLE_YOUTUBE_API_KEY", "k"), \
             patch.object(event_tools.requests, "get", side_effect=fake_get):
            return json.loads(check_event_video_duration.invoke({"event_id": "do_1"}))

    def test_short_video_is_missing(self):
        out = self._run(EMBED, {"items": [{"contentDetails": {"duration": "PT45S"}}]})
        assert out["status"] == "video_missing"

    def test_zero_second_video_is_missing(self):
        out = self._run(EMBED, {"items": [{"contentDetails": {"duration": "PT0S"}}]})
        assert out["status"] == "video_missing"

    def test_exactly_one_minute_is_valid(self):
        out = self._run(EMBED, {"items": [{"contentDetails": {"duration": "PT1M"}}]})
        assert out["status"] == "video_valid"

    def test_no_video_id_unverifiable(self):
        assert self._run("https://example.com/x")["status"] == "unverifiable"

    def test_youtube_empty_unverifiable(self):
        assert self._run(EMBED, {"items": []})["status"] == "unverifiable"

    def test_youtube_error_unverifiable(self):
        assert self._run(EMBED, yt_raises=True)["status"] == "unverifiable"


class TestVideoConfig:
    def _run(self, link):
        with patch.object(event_tools.requests, "get",
                          return_value=_resp({"result": {"event": {"registrationLink": link}}})):
            return json.loads(check_event_video_config.invoke({"event_id": "do_1"}))["status"]

    def test_missing(self):
        assert self._run(None) == "link_missing"

    def test_invalid(self):
        assert self._run("https://example.com/v") == "link_invalid"

    def test_valid(self):
        assert self._run(EMBED) == "config_valid"

    def test_error(self):
        with patch.object(event_tools.requests, "get", side_effect=RuntimeError("boom")):
            out = json.loads(check_event_video_config.invoke({"event_id": "do_1"}))
        assert out["status"] == "error"


class TestDiagnoseProgress:
    def test_certificate_issued_is_complete(self):
        assert _run_progress(_event_item(time_spent=10, certs=[{"x": 1}]))["status"] == "already_complete"

    def test_full_completion_is_complete(self):
        assert _run_progress(_event_item(completion=100.0))["status"] == "already_complete"

    def test_missing_time_spent_is_in_progress(self):
        assert _run_progress(_event_item())["status"] == "in_progress"

    def test_exactly_600_is_in_progress(self):
        assert _run_progress(_event_item(time_spent=600))["status"] == "in_progress"

    def test_over_600_is_technical_issue(self):
        assert _run_progress(_event_item(time_spent=601))["status"] == "technical_issue"

    def test_not_enrolled(self):
        with patch.object(event_tools.requests, "post", return_value=_user([{"id": "u1"}])), \
             patch.object(event_tools.requests, "get", return_value=_events([])):
            out = json.loads(diagnose_event_progress.invoke({"event_id": "do_1", "email": "a@b.c"}))
        assert out["status"] == "not_enrolled"


def test_tool_list():
    assert len(get_event_tools()) == 4


def test_video_duration_unverifiable_when_youtube_url_not_configured():
    with patch.object(event_tools, "GOOGLE_YOUTUBE_API_BASE_URL", None), \
         patch.object(event_tools.requests, "get",
                      return_value=_resp({"result": {"event": {"registrationLink": EMBED}}})):
        out = json.loads(check_event_video_duration.invoke({"event_id": "do_1"}))
    assert out["status"] == "unverifiable"
