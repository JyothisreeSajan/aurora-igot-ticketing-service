"""Tests for the private 'out of scope' Zoho comment (category_disabled path)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.graph.main_graph import early_exit_node
from app.core.tools import zoho_tools
from app.services import zoho_service
from app.services.zoho_service import ZohoAPIError, add_private_comment


def _resp(status=200, json_data=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data or {}
    r.text = text
    return r


def _client(get=None, post=None):
    c = MagicMock()
    c.get = AsyncMock(side_effect=get) if isinstance(get, list) else AsyncMock(return_value=get)
    c.post = AsyncMock(side_effect=post) if isinstance(post, list) else AsyncMock(return_value=post)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=c)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, c


@pytest.mark.anyio
@patch("app.services.zoho_service.get_valid_access_token", new_callable=AsyncMock, return_value="tok")
async def test_comment_posted_as_private(_tok):
    cm, c = _client(get=_resp(204), post=_resp(200, {"id": "c1"}))
    with patch("app.services.zoho_service.httpx.AsyncClient", return_value=cm):
        result = await add_private_comment("T1", "hello")
    assert result == {"id": "c1"}
    body = c.post.call_args.kwargs["json"]
    assert body["isPublic"] is False
    assert body["content"] == "hello"
    assert c.post.call_args.args[0].endswith("/tickets/T1/comments")


@pytest.mark.anyio
@patch("app.services.zoho_service.get_valid_access_token", new_callable=AsyncMock, return_value="tok")
async def test_duplicate_comment_skipped(_tok):
    cm, c = _client(get=_resp(200, {"data": [{"content": "hello "}]}), post=_resp(200, {"id": "c1"}))
    with patch("app.services.zoho_service.httpx.AsyncClient", return_value=cm):
        result = await add_private_comment("T1", "hello")
    assert result["skipped"] is True
    c.post.assert_not_called()


@pytest.mark.anyio
@patch("app.services.zoho_service.asyncio.sleep", new_callable=AsyncMock)
@patch("app.services.zoho_service.get_valid_access_token", new_callable=AsyncMock, return_value="tok")
async def test_retries_then_succeeds(_tok, _sleep):
    cm, c = _client(get=_resp(204), post=[_resp(500, text="boom"), _resp(200, {"id": "c2"})])
    with patch("app.services.zoho_service.httpx.AsyncClient", return_value=cm):
        result = await add_private_comment("T1", "hello")
    assert result == {"id": "c2"}
    assert c.post.call_count == 2


@pytest.mark.anyio
@patch("app.services.zoho_service.asyncio.sleep", new_callable=AsyncMock)
@patch("app.services.zoho_service.get_valid_access_token", new_callable=AsyncMock, return_value="tok")
async def test_raises_after_max_attempts(_tok, _sleep):
    cm, c = _client(get=_resp(204), post=_resp(500, text="boom"))
    with patch("app.services.zoho_service.httpx.AsyncClient", return_value=cm):
        with pytest.raises(ZohoAPIError):
            await add_private_comment("T1", "hello")
    assert c.post.call_count == 3


def test_wrapper_never_raises():
    with patch.object(zoho_tools, "ENABLE_ZOHO_TICKET_UPDATE", True), \
         patch("app.services.zoho_service.add_private_comment", new_callable=AsyncMock,
               side_effect=ZohoAPIError("x")):
        assert zoho_tools.add_zoho_ticket_comment("T1", "hi") is False


def test_wrapper_respects_feature_flag():
    with patch.object(zoho_tools, "ENABLE_ZOHO_TICKET_UPDATE", False), \
         patch("app.services.zoho_service.add_private_comment", new_callable=AsyncMock) as m:
        assert zoho_tools.add_zoho_ticket_comment("T1", "hi") is False
    m.assert_not_called()


@patch("app.core.tools.ticket_tools.log_ticket_outcome")
@patch("app.core.tools.zoho_tools.add_zoho_ticket_comment")
def test_early_exit_comments_only_for_disabled_category(mock_comment, _log):
    early_exit_node({"ticket_id": "T1", "is_category_disabled": True, "is_resolved": True})
    mock_comment.assert_called_once_with("T1", zoho_tools.OUT_OF_SCOPE_COMMENT)

    mock_comment.reset_mock()
    early_exit_node({"ticket_id": "T2", "is_junk": True, "is_resolved": True})
    early_exit_node({"ticket_id": "T3", "is_resolved": True})
    mock_comment.assert_not_called()


# ── _run_coro_sync ────────────────────────────────────────────────────────────

async def _answer():
    return 42


def test_run_coro_sync_without_running_loop():
    assert zoho_tools._run_coro_sync(_answer(), timeout=5) == 42


@pytest.mark.anyio
async def test_run_coro_sync_inside_running_loop():
    assert zoho_tools._run_coro_sync(_answer(), timeout=5) == 42


# ── add_zoho_ticket_comment ───────────────────────────────────────────────────

def test_wrapper_success_when_flag_on():
    with patch.object(zoho_tools, "ENABLE_ZOHO_TICKET_UPDATE", True), \
         patch("app.services.zoho_service.add_private_comment", new_callable=AsyncMock,
               return_value={"id": "c1"}) as m:
        assert zoho_tools.add_zoho_ticket_comment("T1", "hi") is True
    m.assert_awaited_once_with("T1", "hi")


# ── zoho_service comment helpers ──────────────────────────────────────────────

@pytest.mark.anyio
@patch("app.services.zoho_service.get_valid_access_token", new_callable=AsyncMock, return_value="tok")
async def test_get_ticket_comments_returns_data(_tok):
    cm, c = _client(get=_resp(200, {"data": [{"content": "a"}]}))
    with patch("app.services.zoho_service.httpx.AsyncClient", return_value=cm):
        assert await zoho_service.get_ticket_comments("T1") == [{"content": "a"}]


@pytest.mark.anyio
@patch("app.services.zoho_service.get_valid_access_token", new_callable=AsyncMock, return_value="tok")
async def test_get_ticket_comments_empty_on_204(_tok):
    cm, _ = _client(get=_resp(204))
    with patch("app.services.zoho_service.httpx.AsyncClient", return_value=cm):
        assert await zoho_service.get_ticket_comments("T1") == []


@pytest.mark.anyio
@patch("app.services.zoho_service.get_valid_access_token", new_callable=AsyncMock, return_value="tok")
async def test_get_ticket_comments_raises_on_error(_tok):
    cm, _ = _client(get=_resp(500, text="boom"))
    with patch("app.services.zoho_service.httpx.AsyncClient", return_value=cm):
        with pytest.raises(ZohoAPIError):
            await zoho_service.get_ticket_comments("T1")


@pytest.mark.anyio
@patch("app.services.zoho_service.get_valid_access_token", new_callable=AsyncMock, return_value="tok")
async def test_comment_still_posted_when_duplicate_check_fails(_tok):
    cm, c = _client(get=_resp(500, text="boom"), post=_resp(200, {"id": "c3"}))
    with patch("app.services.zoho_service.httpx.AsyncClient", return_value=cm):
        assert await add_private_comment("T1", "hello") == {"id": "c3"}
    c.post.assert_called_once()


@pytest.mark.anyio
@patch("app.services.zoho_service.get_valid_access_token", new_callable=AsyncMock, return_value="tok")
async def test_skip_if_exists_false_does_not_fetch_comments(_tok):
    cm, c = _client(get=_resp(200, {"data": [{"content": "hello"}]}), post=_resp(200, {"id": "c4"}))
    with patch("app.services.zoho_service.httpx.AsyncClient", return_value=cm):
        assert await add_private_comment("T1", "hello", skip_if_exists=False) == {"id": "c4"}
    c.get.assert_not_called()


# ── update_zoho_ticket_direct (uses the shared _run_coro_sync) ────────────────

def test_update_ticket_direct_creates_draft_and_tags():
    with patch.object(zoho_tools, "ENABLE_ZOHO_TICKET_UPDATE", True), \
         patch("app.services.zoho_service.get_ticket_details", new_callable=AsyncMock,
               return_value={"email": "u@gov.in"}), \
         patch("app.services.zoho_service.create_draft_reply", new_callable=AsyncMock,
               return_value={"id": "d1", "status": "DRAFT"}) as draft, \
         patch("app.services.zoho_service.ensure_aurora_tag", new_callable=AsyncMock) as tag:
        assert zoho_tools.update_zoho_ticket_direct("T1", "body") == "d1"
    draft.assert_awaited_once_with(ticket_id="T1", content="body", to="u@gov.in")
    tag.assert_awaited_once_with("T1")


def test_update_ticket_direct_skips_tag_when_no_draft_id_and_survives_missing_email():
    with patch.object(zoho_tools, "ENABLE_ZOHO_TICKET_UPDATE", True), \
         patch("app.services.zoho_service.get_ticket_details", new_callable=AsyncMock,
               side_effect=ZohoAPIError("x")), \
         patch("app.services.zoho_service.create_draft_reply", new_callable=AsyncMock,
               return_value={}), \
         patch("app.services.zoho_service.ensure_aurora_tag", new_callable=AsyncMock) as tag:
        assert zoho_tools.update_zoho_ticket_direct("T1", "body") == "unknown"
    tag.assert_not_called()


def test_update_ticket_direct_tag_failure_is_swallowed():
    with patch.object(zoho_tools, "ENABLE_ZOHO_TICKET_UPDATE", True), \
         patch("app.services.zoho_service.get_ticket_details", new_callable=AsyncMock,
               return_value={"email": "u@gov.in"}), \
         patch("app.services.zoho_service.create_draft_reply", new_callable=AsyncMock,
               return_value={"id": "d1"}), \
         patch("app.services.zoho_service.ensure_aurora_tag", new_callable=AsyncMock,
               side_effect=ZohoAPIError("x")):
        assert zoho_tools.update_zoho_ticket_direct("T1", "body") == "d1"


def test_update_ticket_direct_returns_empty_on_errors():
    with patch.object(zoho_tools, "ENABLE_ZOHO_TICKET_UPDATE", True), \
         patch("app.services.zoho_service.get_ticket_details", new_callable=AsyncMock,
               return_value={"email": "u@gov.in"}), \
         patch("app.services.zoho_service.create_draft_reply", new_callable=AsyncMock,
               side_effect=ZohoAPIError("x")):
        assert zoho_tools.update_zoho_ticket_direct("T1", "body") == ""
    with patch.object(zoho_tools, "ENABLE_ZOHO_TICKET_UPDATE", True), \
         patch("app.services.zoho_service.get_ticket_details", new_callable=AsyncMock,
               return_value={"email": "u@gov.in"}), \
         patch("app.services.zoho_service.create_draft_reply", new_callable=AsyncMock,
               side_effect=RuntimeError("x")):
        assert zoho_tools.update_zoho_ticket_direct("T1", "body") == ""


def test_update_ticket_direct_disabled_by_flag():
    with patch.object(zoho_tools, "ENABLE_ZOHO_TICKET_UPDATE", False):
        assert zoho_tools.update_zoho_ticket_direct("T1", "body") == ""
