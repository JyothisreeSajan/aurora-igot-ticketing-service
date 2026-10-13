"""
tests/test_zoho_conversations.py
----------------------------------
Unit tests for the Zoho Conversation History feature introduced in:
  - Commit c812de9: "Update ticket resolution and Zoho conversation handling"

Covers:
  1. is_system_or_bounce()          — bounce/daemon detection helper
  2. get_ticket_conversations_raw() — raw API fetcher
  3. get_ticket_conversations()     — full pipeline (fetch, filter, parse, sort, window)
  4. graph_router._build_ticket_dict() — continuation detection via conversations
  5. graph_router.ingest_ticket()   — parallel asyncio.gather fetch path
  6. base_subgraph.plan_node()      — updated role tagging + internal-note guardrail
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─────────────────────────────────────────────────────────────────
# 1. is_system_or_bounce()
# ─────────────────────────────────────────────────────────────────

from app.services.zoho_service import is_system_or_bounce


class TestIsSystemOrBounce:
    def test_mailer_daemon_in_author_name(self):
        item = {"author": {"name": "MAILER-DAEMON"}, "fromEmailAddress": "", "summary": ""}
        assert is_system_or_bounce(item) is True

    def test_mailer_daemon_in_from_address(self):
        item = {"author": {}, "fromEmailAddress": "mailer-daemon@example.com", "summary": ""}
        assert is_system_or_bounce(item) is True

    def test_mail_delivery_software_in_summary(self):
        item = {"author": {}, "fromEmailAddress": "", "summary": "Mail Delivery Software error"}
        assert is_system_or_bounce(item) is True

    def test_could_not_be_delivered_in_summary(self):
        item = {"author": {}, "fromEmailAddress": "", "summary": "Your message could not be delivered"}
        assert is_system_or_bounce(item) is True

    def test_normal_message_returns_false(self):
        item = {"author": {"name": "Ramesh Kumar"}, "fromEmailAddress": "ramesh@gov.in", "summary": "Ticket update"}
        assert is_system_or_bounce(item) is False

    def test_missing_fields_returns_false(self):
        """None / missing values should not crash and should return False."""
        assert is_system_or_bounce({}) is False

    def test_none_author_fields_handled(self):
        item = {"author": None, "fromEmailAddress": None, "summary": None}
        assert is_system_or_bounce(item) is False

    def test_case_insensitive_detection(self):
        item = {"author": {"name": "Mailer-Daemon"}, "fromEmailAddress": "", "summary": ""}
        assert is_system_or_bounce(item) is True


# ─────────────────────────────────────────────────────────────────
# 2. get_ticket_conversations_raw()
# ─────────────────────────────────────────────────────────────────

from app.services.zoho_service import get_ticket_conversations_raw


class TestGetTicketConversationsRaw:
    @patch("app.services.zoho_service.get_valid_access_token", new_callable=AsyncMock)
    @patch("app.services.zoho_service.httpx.AsyncClient")
    def test_returns_json_on_success(self, mock_client_cls, mock_token):
        mock_token.return_value = "test_token"
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"data": [{"id": "c1"}]}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = _run(get_ticket_conversations_raw("TKT-001"))
        assert result == {"data": [{"id": "c1"}]}

    @patch("app.services.zoho_service.get_valid_access_token", new_callable=AsyncMock)
    @patch("app.services.zoho_service.httpx.AsyncClient")
    def test_raises_on_http_error(self, mock_client_cls, mock_token):
        import httpx
        mock_token.return_value = "test_token"
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(Exception):
            _run(get_ticket_conversations_raw("TKT-001"))


# ─────────────────────────────────────────────────────────────────
# 3. get_ticket_conversations() — Full pipeline
# ─────────────────────────────────────────────────────────────────

from app.services.zoho_service import get_ticket_conversations


class TestGetTicketConversations:

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_returns_empty_list_on_api_error(self, mock_raw):
        mock_raw.side_effect = Exception("Zoho API down")
        result = _run(get_ticket_conversations("TKT-001"))
        assert result == []

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_returns_empty_list_when_data_not_list(self, mock_raw):
        mock_raw.return_value = {"data": "unexpected_string"}
        result = _run(get_ticket_conversations("TKT-001"))
        assert result == []

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_filters_out_bounce_emails(self, mock_raw):
        mock_raw.return_value = {"data": [
            {
                "type": "thread", "direction": "in",
                "author": {"name": "MAILER-DAEMON"},
                "fromEmailAddress": "mailer-daemon@host.com",
                "content": "Delivery failure",
                "createdTime": "2026-10-01T10:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert result == []

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_filters_out_description_thread(self, mock_raw):
        mock_raw.return_value = {"data": [
            {
                "type": "thread", "isDescriptionThread": True,
                "direction": "in", "author": {"name": "User"},
                "content": "Original ticket description",
                "createdTime": "2026-10-01T09:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert result == []

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_parses_inbound_thread_as_user(self, mock_raw):
        mock_raw.return_value = {"data": [
            {
                "type": "thread", "direction": "in",
                "author": {"name": "Priya Singh"},
                "content": "Hello, I need help.",
                "createdTime": "2026-10-01T10:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "Priya Singh" in result[0]["author"]
        assert result[0]["content"] == "Hello, I need help."
        assert result[0]["is_public"] is True

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_parses_outbound_thread_as_agent(self, mock_raw):
        mock_raw.return_value = {"data": [
            {
                "type": "thread", "direction": "out",
                "author": {"name": "Support Agent"},
                "content": "Hi, please try logging in again.",
                "createdTime": "2026-10-01T11:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert len(result) == 1
        assert result[0]["role"] == "agent"
        assert "Support Agent" in result[0]["author"]

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_parses_public_comment_as_agent(self, mock_raw):
        mock_raw.return_value = {"data": [
            {
                "type": "comment", "isPublic": True,
                "commenter": {"name": "L1 Agent Ravi"},
                "content": "We are looking into this.",
                "commentedTime": "2026-10-01T12:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert len(result) == 1
        assert result[0]["role"] == "agent"
        assert "L1 Agent Ravi" in result[0]["author"]
        assert result[0]["is_public"] is True

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_parses_private_comment_as_internal_note(self, mock_raw):
        mock_raw.return_value = {"data": [
            {
                "type": "comment", "isPublic": False,
                "commenter": {"name": "Senior Agent"},
                "content": "Internal: user already escalated.",
                "commentedTime": "2026-10-01T13:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert len(result) == 1
        assert result[0]["role"] == "internal_note"
        assert result[0]["is_public"] is False

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_skips_comment_with_empty_content(self, mock_raw):
        mock_raw.return_value = {"data": [
            {
                "type": "comment", "isPublic": True,
                "commenter": {"name": "Agent"},
                "content": "", "encodedContent": "",
                "commentedTime": "2026-10-01T12:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert result == []

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_skips_thread_with_empty_content(self, mock_raw):
        mock_raw.return_value = {"data": [
            {
                "type": "thread", "direction": "in",
                "author": {"name": "User"},
                "content": "", "summary": "",
                "createdTime": "2026-10-01T10:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert result == []

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_sorts_results_chronologically(self, mock_raw):
        mock_raw.return_value = {"data": [
            {
                "type": "thread", "direction": "out",
                "author": {"name": "Agent"}, "content": "Reply from agent",
                "createdTime": "2026-10-01T12:00:00Z",
            },
            {
                "type": "thread", "direction": "in",
                "author": {"name": "User"}, "content": "First message from user",
                "createdTime": "2026-10-01T10:00:00Z",
            },
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "agent"

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_max_turns_sliding_window(self, mock_raw):
        """Only the last max_turns messages should be returned."""
        items = [
            {
                "type": "thread", "direction": "in",
                "author": {"name": "User"}, "content": f"Message {i}",
                "createdTime": f"2026-10-01T{i:02d}:00:00Z",
            }
            for i in range(10)
        ]
        mock_raw.return_value = {"data": items}
        result = _run(get_ticket_conversations("TKT-001", max_turns=3))
        assert len(result) == 3
        assert result[-1]["content"] == "Message 9"

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_default_max_turns_is_seven(self, mock_raw):
        items = [
            {
                "type": "thread", "direction": "in",
                "author": {"name": "User"}, "content": f"Message {i}",
                "createdTime": f"2026-10-01T{i:02d}:00:00Z",
            }
            for i in range(10)
        ]
        mock_raw.return_value = {"data": items}
        result = _run(get_ticket_conversations("TKT-001"))
        assert len(result) == 7

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_thread_uses_summary_fallback_when_no_content(self, mock_raw):
        mock_raw.return_value = {"data": [
            {
                "type": "thread", "direction": "in",
                "author": {"name": "User"},
                "content": None,
                "summary": "This is the summary fallback",
                "createdTime": "2026-10-01T10:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert len(result) == 1
        assert result[0]["content"] == "This is the summary fallback"

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_comment_uses_encodedContent_fallback(self, mock_raw):
        mock_raw.return_value = {"data": [
            {
                "type": "comment", "isPublic": True,
                "commenter": {"name": "Agent"},
                "content": None,
                "encodedContent": "Fallback encoded content",
                "commentedTime": "2026-10-01T12:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert len(result) == 1
        assert "Fallback encoded content" in result[0]["content"]

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_non_dict_items_in_data_are_skipped(self, mock_raw):
        mock_raw.return_value = {"data": [
            "not_a_dict", None, 42,
            {
                "type": "thread", "direction": "in",
                "author": {"name": "User"}, "content": "Valid message",
                "createdTime": "2026-10-01T10:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert len(result) == 1
        assert result[0]["content"] == "Valid message"

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_timestamp_uses_commentedTime_for_comments(self, mock_raw):
        mock_raw.return_value = {"data": [
            {
                "type": "comment", "isPublic": True,
                "commenter": {"name": "Agent"},
                "content": "Some note",
                "commentedTime": "2026-10-02T09:00:00Z",
                "createdTime": "2026-10-02T08:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert result[0]["timestamp"] == "2026-10-02T09:00:00Z"

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_thread_author_defaults_when_missing(self, mock_raw):
        """Thread with empty author dict should default to 'User' for direction=in."""
        mock_raw.return_value = {"data": [
            {
                "type": "thread", "direction": "in",
                "author": {},
                "content": "A message",
                "createdTime": "2026-10-01T10:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert "User" in result[0]["author"]

    @patch("app.services.zoho_service.get_ticket_conversations_raw", new_callable=AsyncMock)
    def test_comment_author_defaults_to_l1_support_agent(self, mock_raw):
        mock_raw.return_value = {"data": [
            {
                "type": "comment", "isPublic": True,
                "commenter": {},
                "content": "Agent reply",
                "commentedTime": "2026-10-01T12:00:00Z",
            }
        ]}
        result = _run(get_ticket_conversations("TKT-001"))
        assert "L1 Support Agent" in result[0]["author"]


# ─────────────────────────────────────────────────────────────────
# 4. _build_ticket_dict() — continuation detection via conversations
# ─────────────────────────────────────────────────────────────────

from app.core.graph.graph_router import _build_ticket_dict


class TestBuildTicketDictContinuationDetection:
    """Tests the new conversations-based continuation detection logic."""

    @patch("app.core.graph.graph_router.ticket_store")
    def test_continuation_detected_when_prior_agent_reply_exists(self, mock_store):
        """If conversations has an agent message, ticket must be flagged as continuation."""
        mock_store.get_ticket.return_value = None
        mock_store.get_open_clarification_ticket.return_value = None

        conversations = [
            {"role": "user", "content": "Hello", "author": "User", "timestamp": "2026-10-01T09:00:00Z"},
            {"role": "agent", "content": "Hi! How can I help?", "author": "Agent", "timestamp": "2026-10-01T10:00:00Z"},
        ]
        _, is_continuation, _ = _run(
            _build_ticket_dict("user@gov.in", "my issue again", "TKT-100", conversations=conversations)
        )
        assert is_continuation is True

    @patch("app.core.graph.graph_router.ticket_store")
    def test_continuation_detected_via_internal_note(self, mock_store):
        """internal_note role must also trigger continuation."""
        mock_store.get_ticket.return_value = None
        mock_store.get_open_clarification_ticket.return_value = None

        conversations = [
            {"role": "user", "content": "Need help", "author": "User", "timestamp": "2026-10-01T09:00:00Z"},
            {"role": "internal_note", "content": "Staff note", "author": "Agent", "timestamp": "2026-10-01T10:00:00Z"},
        ]
        _, is_continuation, _ = _run(
            _build_ticket_dict("user@gov.in", "follow up", "TKT-101", conversations=conversations)
        )
        assert is_continuation is True

    @patch("app.core.graph.graph_router.ticket_store")
    def test_no_continuation_when_only_user_messages(self, mock_store):
        """Only user messages in conversations — not a continuation."""
        mock_store.get_ticket.return_value = None
        mock_store.get_open_clarification_ticket.return_value = None

        conversations = [
            {"role": "user", "content": "First message", "author": "User", "timestamp": "2026-10-01T09:00:00Z"},
        ]
        _, is_continuation, _ = _run(
            _build_ticket_dict("user@gov.in", "follow up", "TKT-102", conversations=conversations)
        )
        assert is_continuation is False

    @patch("app.core.graph.graph_router.ticket_store")
    def test_no_continuation_when_conversations_is_none(self, mock_store):
        mock_store.get_ticket.return_value = None
        mock_store.get_open_clarification_ticket.return_value = None

        _, is_continuation, _ = _run(
            _build_ticket_dict("user@gov.in", "brand new issue", None, conversations=None)
        )
        assert is_continuation is False

    @patch("app.core.graph.graph_router.ticket_store")
    def test_no_continuation_when_conversations_is_empty(self, mock_store):
        mock_store.get_ticket.return_value = None
        mock_store.get_open_clarification_ticket.return_value = None

        _, is_continuation, _ = _run(
            _build_ticket_dict("user@gov.in", "fresh ticket", "TKT-104", conversations=[])
        )
        assert is_continuation is False

    @patch("app.core.graph.graph_router.ticket_store")
    def test_is_continuation_true_when_agent_in_conversations(self, mock_store):
        """Redundancy check — agent messages always set is_continuation=True."""
        mock_store.get_ticket.return_value = None
        mock_store.get_open_clarification_ticket.return_value = None

        conversations = [
            {"role": "agent", "content": "We replied.", "author": "Agent", "timestamp": "2026-10-01T10:00:00Z"},
        ]
        _, is_continuation, _ = _run(
            _build_ticket_dict("user@gov.in", "new message", "TKT-103", conversations=conversations)
        )
        assert is_continuation is True


# ─────────────────────────────────────────────────────────────────
# 5. ingest_ticket() — parallel asyncio.gather fetch
# ─────────────────────────────────────────────────────────────────

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


class TestIngestTicketParallelFetch:

    @patch("app.core.graph.graph_router.produce_ticket")
    @patch("app.core.graph.graph_router._build_ticket_dict")
    @patch("app.core.graph.graph_router.get_ticket_conversations")
    @patch("app.core.graph.graph_router.get_cleaned_ticket_details")
    @patch("app.core.graph.graph_router.ticket_tracker")
    def test_ingest_fetches_conversations_in_parallel(
        self, mock_tracker, mock_details, mock_convos, mock_build, mock_produce
    ):
        """Both get_cleaned_ticket_details and get_ticket_conversations must be called."""
        mock_details.return_value = {
            "id": "TKT-200", "email": "user@gov.in",
            "message": "Certificate issue", "channel": "email"
        }
        mock_convos.return_value = []
        mock_build.return_value = (
            {"ticket_id": "TKT-200", "email": "user@gov.in", "message": "Certificate issue"},
            False, None
        )
        mock_produce.return_value = True

        response = client.post("/api/v1/resolution/ingest", json={
            "id": "TKT-200", "email": "user@gov.in",
            "message": "Certificate issue", "channel": "email"
        })
        assert response.status_code == 200
        mock_details.assert_called_once_with("TKT-200")
        mock_convos.assert_called_once_with("TKT-200")

    @patch("app.core.graph.graph_router.produce_ticket")
    @patch("app.core.graph.graph_router._build_ticket_dict")
    @patch("app.core.graph.graph_router.get_ticket_conversations")
    @patch("app.core.graph.graph_router.get_cleaned_ticket_details")
    @patch("app.core.graph.graph_router.ticket_tracker")
    def test_ingest_passes_conversations_to_build_ticket_dict(
        self, mock_tracker, mock_details, mock_convos, mock_build, mock_produce
    ):
        """Conversations returned must be forwarded as kwarg to _build_ticket_dict."""
        sample_convos = [{"role": "agent", "content": "We replied.", "author": "Agent (Agent)", "timestamp": "2026-10-01T10:00:00Z"}]
        mock_details.return_value = {
            "id": "TKT-201", "email": "user@gov.in",
            "message": "Login issue", "channel": "email"
        }
        mock_convos.return_value = sample_convos
        mock_build.return_value = (
            {"ticket_id": "TKT-201", "email": "user@gov.in", "message": "Login issue"},
            True, "TKT-201"
        )
        mock_produce.return_value = True

        response = client.post("/api/v1/resolution/ingest", json={
            "id": "TKT-201", "email": "user@gov.in",
            "message": "Login issue", "channel": "email"
        })
        assert response.status_code == 200
        call_kwargs = mock_build.call_args
        assert call_kwargs.kwargs.get("conversations") == sample_convos

    @patch("app.core.graph.graph_router.get_ticket_conversations")
    @patch("app.core.graph.graph_router.get_cleaned_ticket_details")
    @patch("app.core.graph.graph_router.ticket_tracker")
    def test_ingest_returns_500_when_parallel_fetch_fails(
        self, mock_tracker, mock_details, mock_convos
    ):
        """If asyncio.gather raises (either fetch fails), a 500 must be returned."""
        mock_details.side_effect = Exception("Zoho API timeout")
        mock_convos.return_value = []

        response = client.post("/api/v1/resolution/ingest", json={
            "id": "TKT-202", "email": "user@gov.in",
            "message": "Error case", "channel": "email"
        })
        assert response.status_code == 500

    @patch("app.core.graph.graph_router.produce_ticket")
    @patch("app.core.graph.graph_router._build_ticket_dict")
    @patch("app.core.graph.graph_router.get_ticket_conversations")
    @patch("app.core.graph.graph_router.get_cleaned_ticket_details")
    @patch("app.core.graph.graph_router.ticket_tracker")
    def test_ingest_works_when_conversations_is_empty_list(
        self, mock_tracker, mock_details, mock_convos, mock_build, mock_produce
    ):
        """Empty conversations list must not break the ingest flow."""
        mock_details.return_value = {"id": "TKT-203", "email": "user@gov.in", "message": "New ticket"}
        mock_convos.return_value = []
        mock_build.return_value = (
            {"ticket_id": "TKT-203", "email": "user@gov.in", "message": "New ticket"},
            False, None
        )
        mock_produce.return_value = True

        response = client.post("/api/v1/resolution/ingest", json={
            "id": "TKT-203", "email": "user@gov.in",
            "message": "New ticket", "channel": "email"
        })
        assert response.status_code == 200
        assert response.json()["status"] == "queued"


# ─────────────────────────────────────────────────────────────────
# 6. base_subgraph.plan_node() — role tagging + internal note guardrail
# ─────────────────────────────────────────────────────────────────

from langchain_core.messages import AIMessage
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph


class _DummySubgraph(BaseSubgraph):
    CATEGORY = "test_category"

    def __init__(self):
        self._tools = []

    def get_tools(self, state):
        return self._tools

    def system_prompt(self, state):
        return "TEST SYSTEM PROMPT"


def _ai_msg(text):
    m = MagicMock(spec=AIMessage)
    m.content = text
    m.usage_metadata = {}
    return m


class TestPlanNodeRoleTagging:

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_internal_note_role_renders_correct_tag(self, mock_llm):
        """role='internal_note' must render as [Internal Note: ...]."""
        mock_llm.invoke.return_value = _ai_msg("Plan X")
        state = {
            "email": "user@gov.in", "message": "follow up",
            "retry_count": 0, "is_continuation": True,
            "conversation_messages": [
                {"role": "internal_note", "author": "Senior Agent", "content": "Escalation note"},
            ],
        }
        _DummySubgraph().plan_node(state)
        human_msg = mock_llm.invoke.call_args[0][0][1].content
        assert "[Internal Note: Senior Agent]" in human_msg

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_agent_role_renders_correct_tag(self, mock_llm):
        """role='agent' must render as [Agent: ...]."""
        mock_llm.invoke.return_value = _ai_msg("Plan Y")
        state = {
            "email": "user@gov.in", "message": "follow up",
            "retry_count": 0, "is_continuation": True,
            "conversation_messages": [
                {"role": "agent", "author": "Support Bot", "content": "We checked your account."},
            ],
        }
        _DummySubgraph().plan_node(state)
        human_msg = mock_llm.invoke.call_args[0][0][1].content
        assert "[Agent: Support Bot]" in human_msg

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_user_role_renders_correct_tag(self, mock_llm):
        """role='user' must render as [User: ...]."""
        mock_llm.invoke.return_value = _ai_msg("Plan Z")
        state = {
            "email": "user@gov.in", "message": "still broken",
            "retry_count": 0, "is_continuation": True,
            "conversation_messages": [
                {"role": "user", "author": "Ramesh Kumar", "content": "Still having issue."},
            ],
        }
        _DummySubgraph().plan_node(state)
        human_msg = mock_llm.invoke.call_args[0][0][1].content
        assert "[User: Ramesh Kumar]" in human_msg

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_internal_note_guardrail_text_present(self, mock_llm):
        """The security guardrail warning about internal notes must appear in the prompt."""
        mock_llm.invoke.return_value = _ai_msg("Plan A")
        state = {
            "email": "user@gov.in", "message": "question",
            "retry_count": 0, "is_continuation": True,
            "conversation_messages": [
                {"role": "internal_note", "author": "Agent", "content": "Do not share this."},
            ],
        }
        _DummySubgraph().plan_node(state)
        human_msg = mock_llm.invoke.call_args[0][0][1].content
        assert "Under no circumstances should you quote or disclose internal staff notes to the user" in human_msg

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_updated_continuation_label_in_prompt(self, mock_llm):
        """The updated label 'reply to the ongoing conversation' must appear."""
        mock_llm.invoke.return_value = _ai_msg("Plan B")
        state = {
            "email": "user@gov.in", "message": "more info",
            "retry_count": 0, "is_continuation": True,
            "conversation_messages": [
                {"role": "agent", "author": "Bot", "content": "Can you share your ID?"},
                {"role": "user", "author": "User", "content": "My ID is 12345"},
            ],
        }
        _DummySubgraph().plan_node(state)
        human_msg = mock_llm.invoke.call_args[0][0][1].content
        assert "reply to the ongoing conversation" in human_msg

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_old_label_not_present(self, mock_llm):
        """Old label 'reply to the agent's last question' must NOT appear."""
        mock_llm.invoke.return_value = _ai_msg("Plan C")
        state = {
            "email": "user@gov.in", "message": "follow up",
            "retry_count": 0, "is_continuation": True,
            "conversation_messages": [
                {"role": "agent", "author": "Agent", "content": "What is your enrollment ID?"},
            ],
        }
        _DummySubgraph().plan_node(state)
        human_msg = mock_llm.invoke.call_args[0][0][1].content
        assert "reply to the agent's last question" not in human_msg

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_mixed_roles_all_rendered_correctly(self, mock_llm):
        """A mix of user, agent, and internal_note must all render with correct tags."""
        mock_llm.invoke.return_value = _ai_msg("Plan D")
        state = {
            "email": "user@gov.in", "message": "please help",
            "retry_count": 0, "is_continuation": True,
            "conversation_messages": [
                {"role": "user", "author": "Priya", "content": "I cannot login."},
                {"role": "agent", "author": "L1 Bot", "content": "Please reset password."},
                {"role": "internal_note", "author": "Senior", "content": "User escalated from Tier-1."},
            ],
        }
        _DummySubgraph().plan_node(state)
        human_msg = mock_llm.invoke.call_args[0][0][1].content
        assert "[User: Priya]" in human_msg
        assert "[Agent: L1 Bot]" in human_msg
        assert "[Internal Note: Senior]" in human_msg

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_author_defaults_when_missing(self, mock_llm):
        """Messages with no 'author' key should fall back to role-specific defaults."""
        mock_llm.invoke.return_value = _ai_msg("Plan E")
        state = {
            "email": "user@gov.in", "message": "issue",
            "retry_count": 0, "is_continuation": True,
            "conversation_messages": [
                {"role": "internal_note", "content": "Staff note with no author"},
                {"role": "agent", "content": "Agent reply with no author"},
                {"role": "user", "content": "User message with no author"},
            ],
        }
        _DummySubgraph().plan_node(state)
        human_msg = mock_llm.invoke.call_args[0][0][1].content
        assert "[Internal Note: L1 Agent]" in human_msg
        assert "[Agent: Support]" in human_msg
        assert "[User: User]" in human_msg

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_internal_note_content_is_pii_masked(self, mock_llm):
        """Internal note content must still have PII masked before reaching the LLM."""
        mock_llm.invoke.return_value = _ai_msg("Plan F")
        state = {
            "email": "user@gov.in", "message": "issue",
            "retry_count": 0, "is_continuation": True,
            "conversation_messages": [
                {"role": "internal_note", "author": "Agent", "content": "User email is secret@gov.in"},
            ],
        }
        _DummySubgraph().plan_node(state)
        human_msg = mock_llm.invoke.call_args[0][0][1].content
        assert "secret@gov.in" not in human_msg
