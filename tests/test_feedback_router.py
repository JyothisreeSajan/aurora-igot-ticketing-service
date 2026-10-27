"""
Unit tests for POST /api/v1/feedback/resolution (app/api/feedback/router.py).

Elasticsearch is replaced by a MagicMock; `search` decides whether feedback
already exists for the ticket and `index` echoes back the document id.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app
import app.api.feedback.router as feedback

client = TestClient(app)

URL = "/api/v1/feedback/resolution"
INDEX = "aurora_resolution_feedback"


def _hits(*hits):
    return {"hits": {"hits": list(hits)}}


@pytest.fixture
def es():
    mock = MagicMock()
    mock.search.return_value = _hits()
    mock.index.side_effect = lambda index, id, document: {"_id": id, "result": "created"}
    with patch.object(feedback.es_manager, "client", mock):
        yield mock


def test_new_ticket_creates_document_keyed_by_ticket_id(es):
    res = client.post(URL, json={"ticket_id": "T-1", "is_helpful": True, "category": "cert"})
    assert res.status_code == 201
    body = res.json()
    assert (body["action"], body["document_id"], body["index"]) == ("created", "T-1", INDEX)

    query = es.search.call_args.kwargs["body"]["query"]
    assert query == {"bool": {"filter": [{"term": {"ticket_id": "T-1"}}]}}
    kwargs = es.index.call_args.kwargs
    assert kwargs["id"] == "T-1"
    assert kwargs["document"]["is_helpful"] is True
    assert kwargs["document"]["category"] == "cert"


def test_existing_feedback_is_updated_in_place(es):
    es.search.return_value = _hits({
        "_id": "legacy-random-id",
        "_source": {"ticket_id": "T-1", "is_helpful": True, "category": "cert",
                    "comments": "old", "l1_support_team": "team-a",
                    "ticket_created_on": "2026-10-01T04:30:00+00:00"},
    })
    res = client.post(URL, json={"ticket_id": "T-1", "is_helpful": False, "comments": "wrong answer"})
    assert res.status_code == 201
    body = res.json()
    assert (body["action"], body["document_id"]) == ("updated", "legacy-random-id")
    assert body["message"] == "Feedback updated successfully."

    es.index.assert_called_once()
    kwargs = es.index.call_args.kwargs
    assert kwargs["id"] == "legacy-random-id"
    doc = kwargs["document"]
    assert doc["is_helpful"] is False
    assert doc["comments"] == "wrong answer"                    # sent → overwritten
    assert doc["category"] == "cert"                            # not sent → kept
    assert doc["l1_support_team"] == "team-a"
    assert doc["ticket_created_on"] == "2026-10-01T04:30:00+00:00"


def test_explicitly_sent_empty_field_overwrites_stored_value(es):
    es.search.return_value = _hits({"_id": "T-1", "_source": {"ticket_id": "T-1", "comments": "old"}})
    client.post(URL, json={"ticket_id": "T-1", "is_helpful": True, "comments": ""})
    assert es.index.call_args.kwargs["document"]["comments"] == ""


def test_lookup_picks_latest_feedback(es):
    client.post(URL, json={"ticket_id": "T-1", "is_helpful": True})
    body = es.search.call_args.kwargs["body"]
    assert body["sort"] == [{"submitted_on": {"order": "desc", "missing": "_last"}}]
    assert body["size"] == 1


def test_returns_500_when_lookup_fails(es):
    es.search.side_effect = RuntimeError("boom")
    res = client.post(URL, json={"ticket_id": "T-1", "is_helpful": True})
    assert res.status_code == 500
    assert "Failed to store feedback" in res.json()["detail"]
    es.index.assert_not_called()


def test_returns_503_without_es_client():
    with patch.object(feedback.es_manager, "client", None):
        res = client.post(URL, json={"ticket_id": "T-1", "is_helpful": True})
    assert res.status_code == 503
