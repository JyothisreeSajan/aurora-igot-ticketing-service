import json
from unittest.mock import patch, MagicMock
from langchain_core.messages import AIMessage
from app.core.graph.nodes.intake_node import intake_node

def test_intake_node_continuation():
    # If is_continuation is True, it should bypass junk detection and classification
    state = {
        "ticket_id": "test_123",
        "email": "user@gov.in",
        "message": "Continuing conversation...",
        "is_continuation": True,
        "category": "certificate",
        "route_to": "certificate_subgraph",
        "quality_reroute_count": 1
    }
    
    with patch("app.core.graph.nodes.intake_node.detect_junk") as mock_detect_junk:
        new_state = intake_node(state)
        
        # Verify that detect_junk was never called
        mock_detect_junk.assert_not_called()
        
        # Verify state preservation
        assert new_state["is_resolved"] is False
        assert new_state["escalated_to_human"] is False
        assert new_state["category"] == "certificate"
        assert new_state["route_to"] == "certificate_subgraph"
        assert len(new_state["graph_plan"]) == 1
        assert new_state["graph_plan"][0]["is_continuation"] is True

def test_intake_node_junk_detection():
    # If a message is classified as junk with high confidence, route it to junk handling
    state = {
        "ticket_id": "test_123",
        "email": "user@gov.in",
        "message": "Hello hello! How are you doing?",
        "is_continuation": False
    }
    
    with patch("app.core.graph.nodes.intake_node.detect_junk", return_value=(True, 0.9, "Irrelevant greeting")) as mock_detect, \
         patch("app.core.graph.nodes.intake_node._llm") as mock_llm:
        
        new_state = intake_node(state)
        
        mock_detect.assert_called_once_with("Hello hello! How are you doing?", ticket_id="test_123", email="user@gov.in")
        mock_llm.invoke.assert_not_called()  # Standard classification should be bypassed
        
        assert new_state["is_junk"] is True
        assert new_state["junk_reason"] == "Irrelevant greeting"
        assert new_state["category"] == "junk"
        assert new_state["main_category"] == "junk"
        assert new_state["confidence"] == 0.9
        assert new_state["is_resolved"] is True
        assert "final_response" in new_state
        assert "graph_plan" in new_state

def test_intake_node_standard_flow():
    # Standard query classification and enrichment
    state = {
        "ticket_id": "test_123",
        "email": "user@gov.in",
        "message": "I completed 'Digital Literacy' but my certificate is missing.",
        "is_continuation": False
    }
    
    mock_llm_response = MagicMock(spec=AIMessage)
    mock_llm_response.content = json.dumps({
        "category": "certificate",
        "main_category": "certificate",
        "confidence": 0.95,
        "reason": "Missing course completion certificate request."
    })
    mock_llm_response.usage_metadata = {}
    
    with patch("app.core.graph.nodes.intake_node.detect_junk", return_value=(False, 0.0, "")) as mock_detect, \
         patch("app.core.graph.nodes.intake_node.fetch_sop_categories", return_value=["certificate", "course"]) as mock_sop, \
         patch("app.core.graph.nodes.intake_node._llm") as mock_llm:
        
        # Mock LLM classifier response
        mock_llm.invoke.return_value = mock_llm_response
        
        new_state = intake_node(state)
        
        # Verifications
        mock_detect.assert_called_once()
        mock_sop.assert_called_once()
        mock_llm.invoke.assert_called_once()
        
        assert new_state["category"] == "certificate"
        assert new_state["main_category"] == "certificate"
        assert new_state["sop_categories"] == ["certificate", "course"]
        assert new_state["confidence"] == 0.95
        assert new_state["is_resolved"] is False
        assert new_state["enriched_context"]["classification_reason"] == "Missing course completion certificate request."
