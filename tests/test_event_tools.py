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


# ── Additional coverage: helpers ─────────────────────────────────────────────

class TestParseJsonField:
    def test_dict_returned_as_is(self):
        assert event_tools._parse_json_field({"a": 1}) == {"a": 1}

    def test_json_string_parsed(self):
        assert event_tools._parse_json_field('{"a": 1}') == {"a": 1}

    def test_json_non_dict_is_empty(self):
        assert event_tools._parse_json_field("[1, 2]") == {}

    def test_invalid_json_is_empty(self):
        assert event_tools._parse_json_field("not json") == {}

    @pytest.mark.parametrize("value", [None, "", "   ", 5])
    def test_empty_or_other_types(self, value):
        assert event_tools._parse_json_field(value) == {}


class TestExtractTimeSpent:
    def test_direct_lrc_progress_details(self):
        assert _extract_time_spent_seconds({"lrcProgressDetails": '{"duration": 42}'}) == 42.0

    def test_non_numeric_duration_is_none(self):
        assert _extract_time_spent_seconds({"lrcProgressDetails": {"duration": "abc"}}) is None

    def test_consumption_entry_not_a_dict(self):
        assert _extract_time_spent_seconds({"userEventConsumption": ["x"]}) is None

    def test_no_progress_at_all(self):
        assert _extract_time_spent_seconds({}) is None


class TestExtractVideoIdEdgeCases:
    def test_urlparse_value_error(self):
        with patch.object(event_tools, "urlparse", side_effect=ValueError("bad")):
            assert _extract_youtube_video_id("https://youtu.be/x") is None

    def test_embed_path_without_id(self):
        assert _extract_youtube_video_id("https://www.youtube.com/embed/") is None

    def test_nocookie_embed(self):
        assert _extract_youtube_video_id(f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}") == VIDEO_ID

    def test_watch_without_v_param(self):
        assert _extract_youtube_video_id("https://www.youtube.com/watch") is None

    def test_id_with_wrong_length_rejected(self):
        assert _extract_youtube_video_id("https://youtu.be/short") is None

    def test_empty_string(self):
        assert _extract_youtube_video_id("") is None


class TestFetchHelpers:
    def test_event_config_non_dict_body(self):
        with patch.object(event_tools.requests, "get", return_value=_resp(["unexpected"])):
            assert event_tools._fetch_event_config("do_1") == {}

    def test_event_config_without_envelope(self):
        with patch.object(event_tools.requests, "get", return_value=_resp({"event": {"a": 1}})):
            assert event_tools._fetch_event_config("do_1") == {"a": 1}

    def test_events_null_list_becomes_empty(self):
        with patch.object(event_tools.requests, "get", return_value=_resp({"result": {"events": None}})):
            assert event_tools._fetch_events("u1") == []

    def test_youtube_duration_not_configured(self):
        with patch.object(event_tools, "GOOGLE_YOUTUBE_API_BASE_URL", None), \
             patch.object(event_tools, "GOOGLE_YOUTUBE_API_KEY", None):
            assert event_tools._fetch_youtube_duration_seconds(VIDEO_ID) is None

    def test_youtube_duration_key_missing(self):
        with patch.object(event_tools, "GOOGLE_YOUTUBE_API_BASE_URL", "https://yt.test"), \
             patch.object(event_tools, "GOOGLE_YOUTUBE_API_KEY", None):
            assert event_tools._fetch_youtube_duration_seconds(VIDEO_ID) is None

    def test_youtube_duration_success(self):
        payload = {"items": [{"contentDetails": {"duration": "PT2M"}}]}
        with patch.object(event_tools, "GOOGLE_YOUTUBE_API_BASE_URL", "https://yt.test/"), \
             patch.object(event_tools, "GOOGLE_YOUTUBE_API_KEY", "k"), \
             patch.object(event_tools.requests, "get", return_value=_resp(payload)) as mock_get:
            assert event_tools._fetch_youtube_duration_seconds(VIDEO_ID) == 120.0
        assert mock_get.call_args.args[0] == "https://yt.test/videos"

    def test_youtube_item_without_content_details(self):
        with patch.object(event_tools, "GOOGLE_YOUTUBE_API_BASE_URL", "https://yt.test"), \
             patch.object(event_tools, "GOOGLE_YOUTUBE_API_KEY", "k"), \
             patch.object(event_tools.requests, "get", return_value=_resp({"items": [{}]})):
            assert event_tools._fetch_youtube_duration_seconds(VIDEO_ID) is None


class TestNameMatching:
    EVENTS = [
        {"event_id": "1", "event_name": "Digital Governance Webinar"},
        {"event_id": "2", "event_name": "Leadership Masterclass Series"},
        {"event_id": "3", "event_name": ""},
        {"event_id": "4", "event_name": "The Event"},  # only stopwords
    ]

    def test_name_tokens_ignores_stopwords(self):
        assert event_tools._name_tokens("The Event on Video") == set()
        assert event_tools._name_tokens(None) == set()

    def test_empty_text_returns_nothing(self):
        assert event_tools._match_events_by_name(self.EVENTS, "  ") == []
        assert event_tools._match_events_by_name(self.EVENTS, None) == []

    def test_name_contained_in_ticket_text(self):
        out = event_tools._match_events_by_name(self.EVENTS, "video missing in Digital Governance Webinar please help")
        assert [e["event_id"] for e in out] == ["1"]

    def test_text_contained_in_name(self):
        out = event_tools._match_events_by_name(self.EVENTS, "masterclass")
        assert [e["event_id"] for e in out] == ["2"]

    def test_token_overlap_match(self):
        out = event_tools._match_events_by_name(self.EVENTS, "governance digital session webinar")
        assert [e["event_id"] for e in out] == ["1"]

    def test_no_match(self):
        assert event_tools._match_events_by_name(self.EVENTS, "completely unrelated zebra") == []

    def test_only_best_scoring_returned(self):
        events = [
            {"event_id": "a", "event_name": "Alpha Beta Gamma Delta Epsilon"},  # 3/5 token overlap
            {"event_id": "b", "event_name": "Alpha Beta Gamma"},  # contained in the text -> wins outright
        ]
        out = event_tools._match_events_by_name(events, "alpha beta gamma zeta")
        assert [e["event_id"] for e in out] == ["b"]

    def test_find_event(self):
        events = [{"contentId": "do_1"}, {"contentId": "do_2"}]
        assert event_tools._find_event(events, "do_2") == {"contentId": "do_2"}
        assert event_tools._find_event(events, "do_9") is None


# ── Additional coverage: tools ───────────────────────────────────────────────

class TestGetUserEventsExtra:
    def test_no_enrolled_events(self):
        with patch.object(event_tools.requests, "post", return_value=_user([{"id": "u1"}])), \
             patch.object(event_tools.requests, "get", return_value=_events([])):
            out = json.loads(get_user_events.invoke({"email": "a@b.c"}))
        assert out == {"found": False, "message": "No enrolled events found for this user."}

    def test_event_name_returns_matches(self):
        items = [_event_item("do_1", "Digital Governance Webinar"), _event_item("do_2", "Leadership Talk")]
        with patch.object(event_tools.requests, "post", return_value=_user([{"id": "u1"}])), \
             patch.object(event_tools.requests, "get", return_value=_events(items)):
            out = json.loads(get_user_events.invoke({"email": "a@b.c", "event_name": "Digital Governance Webinar"}))
        assert out["event_name_given"] is True
        assert out["count"] == 2
        assert [m["event_id"] for m in out["matches"]] == ["do_1"]
        assert "events" not in out

    def test_event_name_without_match_returns_empty_matches(self):
        with patch.object(event_tools.requests, "post", return_value=_user([{"id": "u1"}])), \
             patch.object(event_tools.requests, "get", return_value=_events([_event_item()])):
            out = json.loads(get_user_events.invoke({"email": "a@b.c", "event_name": "zebra crossing"}))
        assert out["matches"] == []

    def test_lookup_error(self):
        with patch.object(event_tools.requests, "post", side_effect=RuntimeError("down")):
            out = json.loads(get_user_events.invoke({"email": "a@b.c"}))
        assert out == {"found": False, "error": "down"}


class TestVideoDurationExtra:
    def test_config_lookup_error(self):
        with patch.object(event_tools.requests, "get", side_effect=RuntimeError("boom")):
            out = json.loads(check_event_video_duration.invoke({"event_id": "do_1"}))
        assert out == {"status": "unverifiable", "reason": "error", "error": "boom"}

    def test_unparseable_duration_is_unverifiable(self):
        event_resp = _resp({"result": {"event": {"registrationLink": EMBED}}})
        yt_resp = _resp({"items": [{"contentDetails": {"duration": "garbage"}}]})
        with patch.object(event_tools, "GOOGLE_YOUTUBE_API_BASE_URL", "https://yt.test"), \
             patch.object(event_tools, "GOOGLE_YOUTUBE_API_KEY", "k"), \
             patch.object(event_tools.requests, "get", side_effect=[event_resp, yt_resp]):
            out = json.loads(check_event_video_duration.invoke({"event_id": "do_1"}))
        assert out["status"] == "unverifiable"
        assert out["reason"] == "youtube_lookup_empty"


class TestDiagnoseProgressExtra:
    def test_user_not_found(self):
        with patch.object(event_tools.requests, "post", return_value=_user([])):
            out = json.loads(diagnose_event_progress.invoke({"event_id": "do_1", "email": "a@b.c"}))
        assert out == {"status": "error", "message": USER_NOT_FOUND_MESSAGE}

    def test_lookup_error(self):
        with patch.object(event_tools.requests, "post", side_effect=RuntimeError("down")):
            out = json.loads(diagnose_event_progress.invoke({"event_id": "do_1", "email": "a@b.c"}))
        assert out == {"status": "error", "error": "down"}

    def test_invalid_completion_treated_as_zero(self):
        item = _event_item(completion="n/a", time_spent=700)
        out = _run_progress(item)
        assert out["completion_percentage"] == 0.0
        assert out["status"] == "technical_issue"

    def test_response_fields(self):
        out = _run_progress(_event_item(name="Webinar", completion=40, time_spent=120))
        assert out == {"status": "in_progress", "event_name": "Webinar", "time_spent_seconds": 120.0,
                       "completion_percentage": 40.0, "certificate_issued": False}
