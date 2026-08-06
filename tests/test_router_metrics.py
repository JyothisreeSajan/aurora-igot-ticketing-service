import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from main import app
from app.core.utils.token_tracker import token_tracker

client = TestClient(app)


def test_token_tracker_get_resolution_time_stats():
    # Setup mock search responses
    mock_es = MagicMock()
    
    # Mocking minimum resolution times (ascending order query)
    mock_res_min = {
        "hits": {
            "hits": [
                {"_source": {"processing_time_s": 5.0, "ticket_id": "t1"}},
                {"_source": {"processing_time_s": 6.5, "ticket_id": "t2"}},
                {"_source": {"processing_time_s": 7.0, "ticket_id": "t3"}},
                {"_source": {"processing_time_s": 8.5, "ticket_id": "t4"}},
                {"_source": {"processing_time_s": 10.0, "ticket_id": "t5"}},
            ]
        }
    }
    
    # Mocking maximum resolution times (descending order query)
    mock_res_max = {
        "hits": {
            "hits": [
                {"_source": {"processing_time_s": 120.0, "ticket_id": "t10"}},
                {"_source": {"processing_time_s": 100.5, "ticket_id": "t9"}},
                {"_source": {"processing_time_s": 90.0, "ticket_id": "t8"}},
                {"_source": {"processing_time_s": 80.5, "ticket_id": "t7"}},
                {"_source": {"processing_time_s": 60.0, "ticket_id": "t6"}},
            ]
        }
    }
    
    # Return different mock results sequentially: first for min query, second for max query
    mock_es.search.side_effect = [mock_res_min, mock_res_max]
    
    with patch.object(token_tracker, "client", mock_es):
        stats = token_tracker.get_resolution_time_stats()
        
        # Validate logic calculations
        assert stats is not None
        assert stats["average_min_resolution_time_s"] == round((5.0 + 6.5 + 7.0 + 8.5 + 10.0) / 5, 2)
        assert stats["average_max_resolution_time_s"] == round((120.0 + 100.5 + 90.0 + 80.5 + 60.0) / 5, 2)
        assert stats["min_resolution_times"] == [5.0, 6.5, 7.0, 8.5, 10.0]
        assert stats["max_resolution_times"] == [120.0, 100.5, 90.0, 80.5, 60.0]


def test_get_tickets_resolution_time_stats_endpoint():
    mock_stats = {
        "average_min_resolution_time_s": 7.4,
        "average_max_resolution_time_s": 90.3,
        "min_resolution_times": [5.0, 6.5, 7.0, 8.5, 10.0],
        "max_resolution_times": [120.0, 100.5, 90.0, 80.5, 60.0],
    }
    
    with patch("app.core.utils.token_tracker.token_tracker.get_resolution_time_stats", return_value=mock_stats):
        response = client.get("/api/v1/resolution/tickets/resolution-time-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["average_min_resolution_time_s"] == 7.4
        assert data["average_max_resolution_time_s"] == 90.3
        assert data["min_resolution_times"] == [5.0, 6.5, 7.0, 8.5, 10.0]
        assert data["max_resolution_times"] == [120.0, 100.5, 90.0, 80.5, 60.0]


def test_get_tickets_resolution_time_stats_unavailable():
    with patch("app.core.utils.token_tracker.token_tracker.get_resolution_time_stats", return_value=None):
        response = client.get("/api/v1/resolution/tickets/resolution-time-stats")
        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"]


def test_get_tickets_agent_stats_endpoint():
    mock_es = MagicMock()
    mock_search_result = {
        "hits": {
            "total": {"value": 10}
        },
        "aggregations": {
            "categories": {
                "buckets": [
                    {"key": "general", "doc_count": 2},
                    {"key": "certificate", "doc_count": 5},
                    {"key": "course", "doc_count": 3}
                ]
            },
            "addressed_by_agent_status": {
                "statuses": {
                    "buckets": [
                        {"key": "resolved", "doc_count": 4},
                        {"key": "escalated", "doc_count": 3},
                        {"key": "open", "doc_count": 1}
                    ]
                }
            }
        }
    }
    mock_es.search.return_value = mock_search_result

    with patch("app.core.utils.ticket_tracker.ticket_tracker.client", mock_es):
        response = client.get("/api/v1/resolution/tickets/agent-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        metrics = data["metrics"]
        assert metrics["total_tickets"] == 10
        assert metrics["general_category_tickets"]["count"] == 2
        assert metrics["general_category_tickets"]["percentage"] == 20.0
        assert metrics["tickets_addressed_by_agent"]["count"] == 8
        assert metrics["tickets_addressed_by_agent"]["percentage"] == 80.0
        
        breakdown = metrics["tickets_addressed_by_agent"]["breakdown"]
        assert breakdown["resolved_by_agent"]["count"] == 4
        assert breakdown["resolved_by_agent"]["percentage"] == 50.0
        assert breakdown["escalated_to_human"]["count"] == 3
        assert breakdown["escalated_to_human"]["percentage"] == 37.5
        assert breakdown["awaiting_clarification"]["count"] == 1
        assert breakdown["awaiting_clarification"]["percentage"] == 12.5


def test_get_tickets_agent_stats_unavailable():
    with patch("app.core.utils.ticket_tracker.ticket_tracker.client", None):
        response = client.get("/api/v1/resolution/tickets/agent-stats")
        assert response.status_code == 503
        assert "not connected" in response.json()["detail"]

