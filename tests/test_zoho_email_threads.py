"""Unit tests for the email-thread body fetching added to zoho_service."""

import asyncio
from unittest.mock import AsyncMock, patch

from app.services import zoho_service as z

HTML = (
    "<div>Hi,</div><div>I am unable to update my mobile number to 8368315162.</div>"
    "<div>Thanks and Regards,</div>"
    "DISCLAIMER: The content of this email is confidential."
)
BODY = "Hi,\nI am unable to update my mobile number to 8368315162.\nThanks and Regards,"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _t(i, direction="in", status="SUCCESS", desc=False, created=None, summary="s"):
    return {
        "id": str(i), "direction": direction, "status": status,
        "isDescriptionThread": desc, "summary": summary,
        "createdTime": created or f"2026-10-13T05:{i:02d}:00.000Z",
        "author": {"name": "Jo"},
    }


def _patch(threads, details):
    return (
        patch.object(z, "get_ticket_threads_raw", AsyncMock(return_value={"data": threads})),
        patch.object(z, "get_thread_detail_raw",
                     AsyncMock(side_effect=lambda tid, th: details[th] if isinstance(details[th], dict) else (_ for _ in ()).throw(details[th]))),
    )


def test_filters_draft_bounce_and_parses_html():
    threads = [
        _t(2, status="DRAFT"),
        {**_t(3), "author": {"name": "MAILER-DAEMON"}},
        _t(4),
    ]
    p1, p2 = _patch(threads, {"4": {"content": HTML}})
    with p1, p2:
        out = _run(z.get_ticket_email_threads("T"))
    assert len(out) == 1
    assert out[0]["role"] == "user" and out[0]["content"] == BODY


def test_latest_10_oldest_first():
    threads = [_t(i) for i in range(1, 16)]
    p1, p2 = _patch(threads, {str(i): {"content": f"msg {i}"} for i in range(1, 16)})
    with p1, p2:
        out = _run(z.get_ticket_email_threads("T"))
    assert [o["content"] for o in out] == [f"msg {i}" for i in range(6, 16)]


def test_summary_fallback_when_content_missing_or_fetch_fails():
    threads = [_t(1, summary="Hello there..."), _t(2, direction="out", summary="Fixed")]
    p1, p2 = _patch(threads, {"1": {"content": ""}, "2": RuntimeError("boom")})
    with p1, p2:
        out = _run(z.get_ticket_email_threads("T"))
    assert [o["content"] for o in out] == ["Hello there", "Fixed"]
    assert out[1]["role"] == "agent"


def test_list_api_failure_returns_empty():
    with patch.object(z, "get_ticket_threads_raw", AsyncMock(side_effect=RuntimeError("x"))):
        assert _run(z.get_ticket_email_threads("T")) == []


def test_description_dropped_when_duplicate_of_thread():
    ticket = {"subject": "S", "description": BODY}
    out = z._merge_description_with_threads(ticket, [{"content": BODY}])
    assert "description" not in out and out["subject"] == "S"


def test_description_kept_when_no_threads_or_different():
    t = {"description": "abc"}
    assert z._merge_description_with_threads(t, []) == t
    assert z._merge_description_with_threads(t, [{"content": "xyz"}]) == t


def test_truncated_summary_upgraded_to_description():
    th = [{"content": "I am unable to update"}]
    z._merge_description_with_threads({"description": "I am unable to update my number"}, th)
    assert th[0]["content"] == "I am unable to update my number"


def test_cleaned_details_puts_threads_first_and_dedups():
    details = {"id": "1", "subject": "Update Mobile Number", "description": HTML}
    threads = [{"role": "user", "author": "Jo (User)", "timestamp": "t", "content": BODY}]
    with patch.object(z, "get_ticket_details", AsyncMock(return_value=details)), \
         patch.object(z, "get_ticket_email_threads", AsyncMock(return_value=threads)):
        out = _run(z.get_cleaned_ticket_details("1"))
    assert list(out)[0] == "email_threads"
    assert "description" not in out and out["subject"] == "Update Mobile Number"


def test_description_thread_is_not_skipped():
    p1, p2 = _patch([_t(1, desc=True)], {"1": {"content": "desc thread body"}})
    with p1, p2:
        out = _run(z.get_ticket_email_threads("T"))
    assert [o["content"] for o in out] == ["desc thread body"]


def test_fallback_order_detail_summary_before_list_summary():
    threads = [_t(1, summary="list summary"), _t(2, summary="list only")]
    p1, p2 = _patch(threads, {"1": {"content": "", "summary": "detail summary..."},
                              "2": {"content": ""}})
    with p1, p2:
        out = _run(z.get_ticket_email_threads("T"))
    assert [o["content"] for o in out] == ["detail summary", "list only"]


# ── Raw API fetchers ─────────────────────────────────────────────────────────

import httpx
import pytest
from unittest.mock import MagicMock


def _mock_http(payload=None, error=None):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock(side_effect=error)
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.parametrize("call, url_suffix, params", [
    (lambda: z.get_ticket_threads_raw("T1", limit=5), "/tickets/T1/threads", {"limit": 5}),
    (lambda: z.get_thread_detail_raw("T1", "th9"), "/tickets/T1/threads/th9", None),
])
def test_raw_fetchers_return_json_and_hit_expected_url(call, url_suffix, params):
    client = _mock_http({"data": "ok"})
    with patch.object(z, "get_valid_access_token", AsyncMock(return_value="tok")), \
         patch.object(z.httpx, "AsyncClient", return_value=client):
        assert _run(call()) == {"data": "ok"}
    args, kwargs = client.get.call_args
    assert args[0].endswith(url_suffix)
    assert kwargs["headers"]["Authorization"] == "Zoho-oauthtoken tok"
    assert kwargs.get("params") == params


@pytest.mark.parametrize("call", [
    lambda: z.get_ticket_threads_raw("T1"),
    lambda: z.get_thread_detail_raw("T1", "th9"),
])
def test_raw_fetchers_raise_on_http_error(call):
    err = httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock())
    with patch.object(z, "get_valid_access_token", AsyncMock(return_value="tok")), \
         patch.object(z.httpx, "AsyncClient", return_value=_mock_http(error=err)):
        with pytest.raises(httpx.HTTPStatusError):
            _run(call())


# ── Edge cases in thread selection / parsing ─────────────────────────────────

def test_non_list_threads_payload_returns_empty():
    with patch.object(z, "get_ticket_threads_raw", AsyncMock(return_value={"data": "bad"})):
        assert _run(z.get_ticket_email_threads("T")) == []


@pytest.mark.parametrize("item", ["not-a-dict", {"status": "SUCCESS"}])
def test_is_real_thread_rejects_malformed_items(item):
    assert z._is_real_thread(item) is False


def test_thread_without_any_text_is_dropped():
    p1, p2 = _patch([_t(1, summary="")], {"1": {"content": ""}})
    with p1, p2:
        assert _run(z.get_ticket_email_threads("T")) == []


def test_plain_text_content_and_default_author_names():
    t_in = {**_t(1), "author": None}
    t_out = {**_t(2, direction="out"), "author": {}}
    p1, p2 = _patch([t_in, t_out], {"1": {"content": "  plain  "}, "2": {"content": "reply"}})
    with p1, p2:
        out = _run(z.get_ticket_email_threads("T"))
    assert [(o["author"], o["content"]) for o in out] == [("User (User)", "plain"), ("Agent (Agent)", "reply")]


# ── extract_email_body ───────────────────────────────────────────────────────

def test_extract_email_body_empty_input():
    assert z.extract_email_body("") == ""


def test_extract_email_body_strips_signature_and_noise_tags():
    html = (
        "<head><title>x</title></head><style>p{}</style><img src='a'>"
        "<div>Hello</div><div id='MailSignature'>Sig</div><div class='zm_signature'>Sig2</div>"
    )
    assert z.extract_email_body(html) == "Hello"
