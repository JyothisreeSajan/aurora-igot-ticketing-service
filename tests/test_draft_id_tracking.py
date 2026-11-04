"""Aurora's Zoho draft ids must be captured on the state and persisted on the ticket doc."""
from unittest.mock import MagicMock, patch

import pytest

from app.core.graph.state import TicketState
from app.core.graph.ticket_store import TicketStore, _DRAFT_PROPERTIES
from app.core.tools import ticket_tools, zoho_tools


@pytest.fixture
def store_client():
    """A TicketStore (built without __init__, which needs a live ES) wired to a mock ES client."""
    client = MagicMock()
    store = TicketStore.__new__(TicketStore)
    with patch("app.core.graph.ticket_store.es_manager") as es, \
         patch("app.core.graph.ticket_store.ES_INDEX", "idx"):
        es.client = client
        yield store, client


def test_state_declares_draft_fields():
    assert "zoho_draft_id" in TicketState.__annotations__
    assert "draft_created" in TicketState.__annotations__


def test_create_ticket_persists_draft_fields(store_client):
    store, client = store_client
    state = {"ticket_id": "T1", "zoho_draft_id": "D1", "draft_created": True}
    with patch.object(store, "_ensure_index"):
        store.create_ticket(state)
    doc = client.index.call_args.kwargs["document"]
    assert doc["zoho_draft_id"] == "D1"
    assert doc["zoho_draft_ids"] == ["D1"]
    assert doc["draft_created"] is True


def test_create_ticket_without_draft_omits_draft_fields(store_client):
    store, client = store_client
    with patch.object(store, "_ensure_index"):
        store.create_ticket({"ticket_id": "T1", "draft_created": False})
    doc = client.index.call_args.kwargs["document"]
    assert not set(_DRAFT_PROPERTIES) & set(doc)


def test_update_ticket_appends_new_draft(store_client):
    store, client = store_client
    store.update_ticket("T1", {"zoho_draft_id": "D2", "draft_created": True})
    # partial doc update first, then the scripted append
    assert client.update.call_count == 2
    script = client.update.call_args.kwargs["body"]["script"]
    assert script["params"]["draft_id"] == "D2"
    assert "zoho_draft_ids.add" in script["source"]


def test_update_ticket_without_draft_leaves_draft_fields_alone(store_client):
    store, client = store_client
    store.update_ticket("T1", {"draft_created": False})
    assert client.update.call_count == 1
    assert not set(_DRAFT_PROPERTIES) & set(client.update.call_args.kwargs["body"]["doc"])


def _resolved_state():
    return {"ticket_id": "T1", "email": "a@b.com", "final_response": "hi", "is_resolved": True}


@patch("app.core.tools.ticket_tools.log_ticket_outcome")
@patch("app.core.tools.ticket_tools.update_zoho_ticket_direct", return_value="D9")
def test_notify_user_records_draft_id(mock_update, mock_log):
    out = ticket_tools.notify_user(_resolved_state())
    assert out["zoho_draft_id"] == "D9" and out["draft_created"] is True
    assert mock_log.call_args.kwargs["outcome"] == "resolved"


@patch("app.core.tools.ticket_tools.log_ticket_outcome")
@patch("app.core.tools.ticket_tools.update_zoho_ticket_direct", return_value="")
def test_notify_user_flags_failed_draft(mock_update, mock_log):
    out = ticket_tools.notify_user(_resolved_state())
    assert out["draft_created"] is False and out["zoho_draft_id"] == ""
    assert mock_log.call_args.kwargs["outcome"] == "draft_failed"


def test_missing_id_in_zoho_response_returns_empty_not_unknown():
    async def _no_id(**kwargs):
        return {"status": "DRAFT"}

    async def _ticket(_):
        return {"email": "a@b.com"}

    with patch.object(zoho_tools, "ENABLE_ZOHO_TICKET_UPDATE", True), \
         patch("app.services.zoho_service.create_draft_reply", _no_id), \
         patch("app.services.zoho_service.get_ticket_details", _ticket):
        assert zoho_tools.update_zoho_ticket_direct("T1", "body") == ""


# ── Coverage for the remaining TicketStore / ticket_tools branches ───────────

def _mapping(props):
    return {"idx": {"mappings": {"properties": props}}}


def test_ensure_index_checks_draft_mapping_when_index_exists(store_client):
    store, client = store_client
    client.indices.exists.return_value = True
    with patch.object(store, "_ensure_draft_mapping") as ensure:
        store._ensure_index()
    ensure.assert_called_once_with(client)
    client.indices.create.assert_not_called()


def test_ensure_draft_mapping_noop_when_fields_present(store_client):
    store, client = store_client
    client.indices.get_mapping.return_value = _mapping(dict(_DRAFT_PROPERTIES))
    store._ensure_draft_mapping(client)
    client.indices.put_mapping.assert_not_called()


def test_ensure_draft_mapping_adds_missing_fields(store_client):
    store, client = store_client
    client.indices.get_mapping.return_value = {"idx": {"mappings": {"_doc": {"properties": {}}}}}
    store._ensure_draft_mapping(client)
    assert client.indices.put_mapping.call_args.kwargs["body"] == {"properties": _DRAFT_PROPERTIES}


def test_ensure_draft_mapping_falls_back_to_typeless_put(store_client):
    store, client = store_client
    client.indices.put_mapping.side_effect = [Exception("include_type_name"), None]
    client.indices.get_mapping.return_value = _mapping({})
    store._ensure_draft_mapping(client)
    assert client.indices.put_mapping.call_count == 2
    assert "doc_type" not in client.indices.put_mapping.call_args.kwargs


def test_ensure_draft_mapping_swallows_errors(store_client):
    store, client = store_client
    client.indices.get_mapping.side_effect = Exception("boom")
    store._ensure_draft_mapping(client)  # must not raise


def test_update_ticket_stops_when_partial_update_fails(store_client):
    store, client = store_client
    client.update.side_effect = Exception("es down")
    store.update_ticket("T1", {"zoho_draft_id": "D2", "draft_created": True})
    assert client.update.call_count == 1  # no scripted append after a failed update


def test_append_draft_swallows_errors(store_client):
    store, client = store_client
    client.update.side_effect = Exception("es down")
    store._append_draft("T1", "D2", "ts")  # must not raise


def test_log_ticket_outcome_swallows_es_errors():
    with patch("app.core.tools.ticket_tools.es_manager") as es:
        es.log_interaction.side_effect = Exception("es down")
        ticket_tools.log_ticket_outcome({"ticket_id": "T1"}, "resolved")  # must not raise


def test_ensure_index_creates_missing_index_with_draft_fields(store_client):
    store, client = store_client
    client.indices.exists.return_value = False
    store._ensure_index()
    props = client.indices.create.call_args.kwargs["body"]["mappings"]["_doc"]["properties"]
    assert set(_DRAFT_PROPERTIES) <= set(props)


def test_ensure_index_skips_without_client(store_client):
    store, client = store_client
    with patch("app.core.graph.ticket_store.es_manager") as es:
        es.client = None
        store._ensure_index()  # must not raise
    client.indices.exists.assert_not_called()


def test_ensure_index_swallows_errors(store_client):
    store, client = store_client
    client.indices.exists.side_effect = Exception("es down")
    store._ensure_index()  # must not raise
