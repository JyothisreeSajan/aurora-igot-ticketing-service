import json
from unittest.mock import patch, MagicMock
from langchain_core.messages import AIMessage
from app.core.graph.ticket_store import (
    _ticket_status,
    _build_messages,
    ticket_store,
    ES_INDEX
)

def test_ticket_status_resolution():
    # Escalated to human
    assert _ticket_status({"escalated_to_human": True}) == "escalated"
    assert _ticket_status({"escalated_to_human": True, "needs_clarification": True}) == "escalated"
    
    # Needs clarification
    assert _ticket_status({"escalated_to_human": False, "needs_clarification": True}) == "open"
    assert _ticket_status({"partial_match": True}) == "open"
    
    # Resolved
    assert _ticket_status({"escalated_to_human": False, "needs_clarification": False}) == "resolved"
    assert _ticket_status({}) == "resolved"

def test_build_messages():
    # Scenario 1: Empty history, user message provided
    state1 = {
        "message": "Hello, I need help.",
        "conversation_messages": []
    }
    msgs1 = _build_messages(state1)
    assert len(msgs1) == 1
    assert msgs1[0]["role"] == "user"
    assert msgs1[0]["content"] == "Hello, I need help."
    
    # Scenario 2: Existing history, new user message and agent response
    state2 = {
        "message": "I want a refund.",
        "conversation_messages": [
            {"role": "user", "content": "Hello", "timestamp": "2026-05-22T08:00:00Z"},
            {"role": "agent", "content": "Hi there!", "timestamp": "2026-05-22T08:01:00Z"}
        ],
        "final_response": "We don't do refunds."
    }
    msgs2 = _build_messages(state2)
    assert len(msgs2) == 4
    assert msgs2[2]["role"] == "user"
    assert msgs2[2]["content"] == "I want a refund."
    assert msgs2[3]["role"] == "agent"
    assert msgs2[3]["content"] == "We don't do refunds."

@patch("app.core.graph.ticket_store.ticket_store._client")
def test_create_ticket(mock_client_getter):
    mock_client = MagicMock()
    mock_client_getter.return_value = mock_client
    
    # Mock indices.exists to return True
    mock_client.indices.exists.return_value = True
    
    state = {
        "ticket_id": "ticket_123",
        "email": "test@gov.in",
        "category": "certificate",
        "message": "Certificate issue",
        "final_response": "Checking your enrollments."
    }
    
    tid = ticket_store.create_ticket(state)
    assert tid == "ticket_123"
    mock_client.index.assert_called_once()
    kwargs = mock_client.index.call_args[1]
    assert kwargs["index"] == ES_INDEX
    assert kwargs["id"] == "ticket_123"
    assert kwargs["document"]["email"] == "test@gov.in"
    assert kwargs["document"]["status"] == "resolved"

@patch("app.core.graph.ticket_store.ticket_store._client")
def test_get_ticket(mock_client_getter):
    mock_client = MagicMock()
    mock_client_getter.return_value = mock_client
    
    mock_client.get.return_value = {
        "_source": {
            "ticket_id": "ticket_abc",
            "email": "abc@gov.in",
            "status": "open"
        }
    }
    
    res = ticket_store.get_ticket("ticket_abc")
    assert res is not None
    assert res["email"] == "abc@gov.in"
    mock_client.get.assert_called_once_with(index=ES_INDEX, id="ticket_abc")

@patch("app.core.graph.ticket_store.ticket_store._client")
def test_get_open_clarification_ticket(mock_client_getter):
    mock_client = MagicMock()
    mock_client_getter.return_value = mock_client
    
    mock_client.search.return_value = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "ticket_id": "ticket_open",
                        "email": "abc@gov.in",
                        "status": "open",
                        "awaiting_clarification": True
                    }
                }
            ]
        }
    }
    
    res = ticket_store.get_open_clarification_ticket("abc@gov.in")
    assert res is not None
    assert res["ticket_id"] == "ticket_open"
    mock_client.search.assert_called_once()

@patch("app.core.graph.ticket_store._llm")
def test_is_continuation_reply(mock_llm):
    mock_llm_response = MagicMock(spec=AIMessage)
    mock_llm_response.content = json.dumps({
        "reply": True,
        "reason": "Directly replies to the certificate question."
    })
    mock_llm.invoke.return_value = mock_llm_response
    
    open_ticket = {
        "ticket_id": "t1",
        "clarification_question": "What is the course name?",
        "messages": [
            {"role": "user", "content": "I finished a course but no certificate."}
        ]
    }
    
    res = ticket_store.is_continuation_reply(open_ticket, "It was 'Digital Literacy'.")
    assert res is True
    mock_llm.invoke.assert_called_once()
