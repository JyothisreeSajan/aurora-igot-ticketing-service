"""
tests/test_ingest_apis.py
--------------------------
Tests for Ticket Ingestion APIs (/ingest), Synchronous Process API (/process),
and Tracking/Admin endpoints.
"""

from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


# ── 1. Ingestion API Tests (/api/v1/resolution/ingest) ─────────────────────────

@patch("app.core.graph.graph_router.produce_ticket")
@patch("app.core.graph.graph_router.get_cleaned_ticket_details")
@patch("app.core.graph.graph_router._build_ticket_dict")
@patch("app.core.graph.graph_router.ticket_tracker")
def test_ingest_ticket_flat_json(mock_tracker, mock_build, mock_get_details, mock_produce):
    """Test /ingest endpoint with flat JSON payload."""
    mock_get_details.return_value = {
        "id": "t_flat_123",
        "email": "user@gov.in",
        "message": "Certificate issue",
        "channel": "email"
    }
    mock_build.return_value = (
        {
            "ticket_id": "t_ingest_flat_123",
            "email": "user@gov.in",
            "message": "Certificate issue"
        },
        False,
        None
    )
    mock_produce.return_value = True

    payload = {
        "id": "t_flat_123",
        "email": "user@gov.in",
        "message": "Certificate issue",
        "channel": "email"
    }

    response = client.post("/api/v1/resolution/ingest", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["ticket_id"] == "t_ingest_flat_123"
    mock_produce.assert_called_once()


@patch("app.core.graph.graph_router.produce_ticket")
@patch("app.core.graph.graph_router.get_cleaned_ticket_details")
@patch("app.core.graph.graph_router._build_ticket_dict")
@patch("app.core.graph.graph_router.ticket_tracker")
def test_ingest_ticket_zoho_nested_payload(mock_tracker, mock_build, mock_get_details, mock_produce):
    """Test /ingest endpoint with Zoho Desk extension payload structure (nested 'ticket' object)."""
    mock_get_details.return_value = {
        "id": "270306000000363001",
        "email": "user@gov.in",
        "message": "Certificate not generating",
        "channel": "Email"
    }
    mock_build.return_value = (
        {
            "ticket_id": "t_ingest_270306000000363001",
            "email": "user@gov.in",
            "message": "Certificate not generating"
        },
        False,
        None
    )
    mock_produce.return_value = True

    payload = {
        "ticket": {
            "id": "270306000000363001",
            "ticketNumber": "1001",
            "subject": "Certificate not generating",
            "description": "Please issue my certificate",
            "status": "Open",
            "channel": "Email",
            "email": "user@gov.in",
        },
        "source": "zoho-desk-extension",
        "timestamp": "2026-07-16T04:44:13.744Z"
    }

    response = client.post("/api/v1/resolution/ingest", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["ticket_id"] == "t_ingest_270306000000363001"
    mock_produce.assert_called_once()


@patch("app.core.graph.graph_router.produce_ticket")
@patch("app.core.graph.graph_router.get_cleaned_ticket_details")
@patch("app.core.graph.graph_router._build_ticket_dict")
@patch("app.core.graph.graph_router.ticket_tracker")
def test_ingest_ticket_list_wrapped_payload(mock_tracker, mock_build, mock_get_details, mock_produce):
    """Test /ingest endpoint with list-wrapped Zoho webhook payload."""
    mock_get_details.return_value = {
        "id": "list_123",
        "email": "listuser@gov.in",
        "message": "List webhook test",
        "channel": "email"
    }
    mock_build.return_value = (
        {
            "ticket_id": "t_ingest_list_123",
            "email": "listuser@gov.in",
            "message": "List webhook test"
        },
        False,
        None
    )
    mock_produce.return_value = True

    payload = [{
        "payload": {
            "id": "list_123",
            "email": "listuser@gov.in",
            "message": "List webhook test",
            "channel": "email"
        },
        "eventType": "Ticket_Add"
    }]

    response = client.post("/api/v1/resolution/ingest", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["ticket_id"] == "t_ingest_list_123"


def test_ingest_ticket_missing_email_validation():
    """Test /ingest returns 400 when email is missing."""
    payload = {
        "id": "999",
        "message": "Missing email payload",
        "channel": "email"
    }
    response = client.post("/api/v1/resolution/ingest", json=payload)
    assert response.status_code == 400
    assert "email is required" in response.json()["detail"]


# ── 2. Synchronous Process API Tests (/api/v1/resolution/process) ────────────

@patch("app.core.graph.graph_router.arun_ticket")
@patch("app.core.graph.graph_router._build_ticket_dict")
@patch("app.core.graph.graph_router._persist")
def test_process_ticket_sync_success(mock_persist, mock_build, mock_arun):
    """Test /process endpoint synchronous ticket execution."""
    mock_build.return_value = (
        {
            "ticket_id": "t_sync_123",
            "email": "syncuser@gov.in",
            "message": "Synchronous test issue"
        },
        False,
        None
    )
    mock_arun.return_value = {
        "ticket_id": "t_sync_123",
        "email": "syncuser@gov.in",
        "is_continuation": False,
        "is_junk": False,
        "category": "certificate",
        "main_category": "certificate",
        "confidence": 0.9,
        "route_to": "certificate_subgraph",
        "final_response": "Hi SyncUser, your certificate is ready.",
        "retry_count": 0,
        "quality_passed": True,
        "graph_plan": []
    }

    payload = {
        "id": "sync_123",
        "email": "syncuser@gov.in",
        "message": "Synchronous test issue",
        "channel": "email"
    }

    response = client.post("/api/v1/resolution/process", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["ticket_id"] == "t_sync_123"
    assert "SyncUser" in data["final_response"]


# ── 3. Health & Admin Endpoints ───────────────────────────────────────────────

def test_health_check_endpoints():
    """Test base application health check routes."""
    resp1 = client.get("/")
    assert resp1.status_code == 200

    resp2 = client.get("/api/v1/health")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ok"


@patch("app.core.graph.graph_router.ticket_store")
def test_cleanup_tickets_endpoint(mock_store):
    """Test ticket store cleanup endpoint."""
    mock_store.delete_old_resolved_tickets.return_value = {"deleted_count": 5, "errors": []}
    response = client.delete("/api/v1/resolution/tickets/cleanup?days_old=7")
    assert response.status_code == 200
    assert response.json()["deleted_count"] == 5
