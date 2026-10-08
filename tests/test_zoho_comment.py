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
