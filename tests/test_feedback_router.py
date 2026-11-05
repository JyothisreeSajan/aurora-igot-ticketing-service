"""
Unit tests for POST /api/v1/feedback/resolution (app/api/feedback/router.py).

Elasticsearch is replaced by a MagicMock: `get` returns the ticket doc (or raises
NotFoundError), `search` decides whether feedback already exists for the
ticket + draft and `index`/`update` record the write.
"""
from unittest.mock import MagicMock, patch

import pytest
from elasticsearch import NotFoundError
from fastapi.testclient import TestClient

from main import app
import app.api.feedback.router as feedback

client = TestClient(app)

URL = "/api/v1/feedback/resolution"
INDEX = "aurora_resolution_feedback"
BODY = {"ticket_id": "T-1", "draft_id": "D-1", "is_helpful": True}


def _hits(*hits):
    return {"hits": {"hits": list(hits)}}


def _not_found():
    return NotFoundError("not found", MagicMock(status=404), {})


@pytest.fixture
def es():
    mock = MagicMock()
    mock.get.return_value = {"_source": {"ticket_id": "T-1", "zoho_draft_id": "D-2", "zoho_draft_ids": ["D-1", "D-2"]}}
    mock.search.return_value = _hits()
    with patch.object(feedback.es_manager, "client", mock):
        yield mock


def test_new_feedback_creates_document_keyed_by_ticket_and_draft(es):
    res = client.post(URL, json={**BODY, "category": "cert"})
    assert res.status_code == 201
    body = res.json()
    assert (body["action"], body["document_id"], body["index"]) == ("created", "T-1_D-1", INDEX)

    kwargs = es.index.call_args.kwargs
    assert kwargs["id"] == "T-1_D-1"
    assert kwargs["document"]["draft_id"] == "D-1"
    assert kwargs["document"]["category"] == "cert"
    es.update.assert_not_called()


def test_ticket_lookup_fetches_only_id_and_draft_fields(es):
    client.post(URL, json=BODY)
    get_kwargs = es.get.call_args.kwargs
    assert get_kwargs["id"] == "T-1"
    assert get_kwargs["source_includes"] == ["ticket_id", "zoho_draft_id", "zoho_draft_ids"]
    search_body = es.search.call_args.kwargs["body"]
    assert search_body["_source"] == ["ticket_id", "draft_id"]
    assert search_body["query"] == {"bool": {"filter": [
        {"term": {"ticket_id": "T-1"}}, {"term": {"draft_id": "D-1"}},
    ]}}


def test_unknown_ticket_returns_404(es):
    es.get.side_effect = _not_found()
    res = client.post(URL, json=BODY)
    assert res.status_code == 404
    assert "T-1" in res.json()["detail"]
    es.index.assert_not_called()
    es.update.assert_not_called()


def test_draft_not_on_ticket_returns_404(es):
    res = client.post(URL, json={**BODY, "draft_id": "D-OTHER"})
    assert res.status_code == 404
    assert "D-OTHER" in res.json()["detail"]
    es.search.assert_not_called()
    es.index.assert_not_called()


def test_ticket_without_drafts_returns_404(es):
    es.get.return_value = {"_source": {"ticket_id": "T-1"}}
    assert client.post(URL, json=BODY).status_code == 404


def test_latest_draft_id_alone_is_accepted(es):
    es.get.return_value = {"_source": {"ticket_id": "T-1", "zoho_draft_id": "D-1"}}
    assert client.post(URL, json=BODY).status_code == 201


@pytest.mark.parametrize("draft", [None, "", "   "])
def test_missing_or_blank_draft_id_is_rejected(es, draft):
    payload = {"ticket_id": "T-1", "is_helpful": True}
    if draft is not None:
        payload["draft_id"] = draft
    assert client.post(URL, json=payload).status_code == 422


def test_existing_feedback_for_draft_is_partially_updated(es):
    es.search.return_value = _hits({"_id": "legacy-id", "_source": {"ticket_id": "T-1", "draft_id": "D-1"}})
    res = client.post(URL, json={**BODY, "is_helpful": False, "comments": "wrong answer"})
    assert res.status_code == 201
    body = res.json()
    assert (body["action"], body["document_id"]) == ("updated", "legacy-id")

    es.index.assert_not_called()
    kwargs = es.update.call_args.kwargs
    assert kwargs["id"] == "legacy-id"
    doc = kwargs["doc"]
    assert doc["is_helpful"] is False
    assert doc["comments"] == "wrong answer"
    assert "category" not in doc and "l1_support_team" not in doc and "ticket_created_on" not in doc


def test_explicitly_sent_empty_field_overwrites_stored_value(existing_feedback):
    es = existing_feedback
    client.post(URL, json={**BODY, "comments": ""})
    assert es.update.call_args.kwargs["doc"]["comments"] == ""


def test_feedback_for_a_second_draft_creates_separate_record(es):
    # no feedback yet for D-2 even though D-1 may have some
    res = client.post(URL, json={**BODY, "draft_id": "D-2"})
    assert res.json()["document_id"] == "T-1_D-2"
    assert res.json()["action"] == "created"


def test_returns_500_when_lookup_fails(es):
    es.search.side_effect = RuntimeError("boom")
    res = client.post(URL, json=BODY)
    assert res.status_code == 500
    assert "Failed to store feedback" in res.json()["detail"]
    es.index.assert_not_called()


def test_returns_503_without_es_client():
    with patch.object(feedback.es_manager, "client", None):
        res = client.post(URL, json=BODY)
    assert res.status_code == 503


@pytest.fixture
def existing_feedback(es):
    es.search.return_value = _hits({"_id": "x", "_source": {}})
    return es


def test_update_writes_every_field_the_caller_sent(existing_feedback):
    es = existing_feedback
    client.post(URL, json={
        **BODY, "category": "cert", "l1_support_team": "L1-A",
        "ticket_created_on": "2026-01-02T03:04:05Z",
    })
    doc = es.update.call_args.kwargs["doc"]
    assert doc["category"] == "cert"
    assert doc["l1_support_team"] == "L1-A"
    assert doc["ticket_created_on"].startswith("2026-01-02T03:04:05")


def test_update_with_null_ticket_created_on_stores_none(existing_feedback):
    es = existing_feedback
    client.post(URL, json={**BODY, "ticket_created_on": None})
    assert es.update.call_args.kwargs["doc"]["ticket_created_on"] is None
