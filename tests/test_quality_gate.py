import json
from unittest.mock import patch, MagicMock
from langchain_core.messages import AIMessage
from app.core.graph.state import TicketState
from app.core.graph.main_graph import quality_gate_node

@patch("app.core.graph.main_graph._llm_quality")
def test_quality_gate_passed(mock_llm):
    # Set up LLM mock for passing check
    mock_response = MagicMock(spec=AIMessage)
    mock_response.content = json.dumps({
        "has_repetition": False,
        "reason": ""
    })
    mock_llm.invoke.return_value = mock_response

    state: TicketState = {
        "ticket_id": "t123",
        "final_response": "Hi Harshit,\n\nHere is the course link.",
        "is_resolved": True,
        "needs_clarification": False,
        "quality_reroute_count": 0,
        "graph_plan": []
    }

    result = quality_gate_node(state)
    assert result["quality_passed"] is True
    assert len(result["quality_issues"]) == 0
    assert result["quality_reroute_count"] == 0
    assert "quality_gate_feedback" not in result
    assert result["is_resolved"] is True

@patch("app.core.graph.main_graph._llm_quality")
def test_quality_gate_failed_repetition(mock_llm):
    # Set up LLM mock for failing check (repetition detected)
    mock_response = MagicMock(spec=AIMessage)
    mock_response.content = json.dumps({
        "has_repetition": True,
        "reason": "Duplicate greeting 'Dear Dr. Harshit Pant' found in body."
    })
    mock_llm.invoke.return_value = mock_response

    state: TicketState = {
        "ticket_id": "t123",
        "final_response": "Hi Dr Harshit Pant,\n\nDear Dr. Harshit Pant,\n\nHere is the course link.",
        "is_resolved": True,
        "needs_clarification": False,
        "quality_reroute_count": 0,
        "graph_plan": []
    }

    result = quality_gate_node(state)
    assert result["quality_passed"] is False
    assert any("Greeting repetition check failed" in issue for issue in result["quality_issues"])
    assert result["quality_reroute_count"] == 1
    assert "quality_gate_feedback" in result
    assert "repeating" in result["quality_gate_feedback"]
    assert result["is_resolved"] is False
