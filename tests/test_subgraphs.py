import json
from unittest.mock import patch, MagicMock
from langchain_core.messages import AIMessage
from app.core.graph.subgraphs.certificate_subgraph import certificate_subgraph, CertificateSubgraph
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.graph.state import TicketState

def test_subgraph_compilation():
    # Verify the compiled certificate subgraph has the expected structure
    assert certificate_subgraph is not None
    # Check that entrypoint/nodes are compiled
    assert hasattr(certificate_subgraph, "invoke")
    assert hasattr(certificate_subgraph, "ainvoke")

def test_should_retry():
    # Create a concrete instance for testing the retry method
    subgraph = CertificateSubgraph()
    
    # Scenario 1: Ticket resolved -> done
    state1 = {"is_resolved": True, "retry_count": 0, "max_retries": 3}
    assert subgraph.should_retry(state1) == "done"
    
    # Scenario 2: Ticket not resolved, retry_count < max_retries -> plan_node
    state2 = {"is_resolved": False, "retry_count": 1, "max_retries": 3}
    assert subgraph.should_retry(state2) == "plan_node"
    
    # Scenario 3: Ticket not resolved, retry_count >= max_retries -> done
    state3 = {"is_resolved": False, "retry_count": 3, "max_retries": 3}
    assert subgraph.should_retry(state3) == "done"

@patch("app.core.graph.subgraphs.base_subgraph._llm")
def test_plan_node(mock_llm):
    subgraph = CertificateSubgraph()
    
    mock_llm_response = MagicMock(spec=AIMessage)
    mock_llm_response.content = "My detailed plan: 1. check user enrollments, 2. reissue if completed."
    mock_llm.invoke.return_value = mock_llm_response
    
    state = {
        "email": "user@gov.in",
        "message": "Certificate missing for john.doe@gov.in, call me at +91-9999999999",
        "main_category": "certificate",
        "retry_count": 0,
        "enriched_context": {"kb_snippets": []}
    }
    
    new_state = subgraph.plan_node(state)
    assert new_state["plan"] == "My detailed plan: 1. check user enrollments, 2. reissue if completed."
    assert len(new_state["graph_plan"]) == 1
    assert "Generated SOP resolution plan" in new_state["graph_plan"][0]["detail"]
    
    # Verify PII masking in plan prompt
    mock_llm.invoke.assert_called_once()
    called_messages = mock_llm.invoke.call_args[0][0]
    human_msg = called_messages[1].content
    assert "john.doe@gov.in" not in human_msg
    assert "+91-9999999999" not in human_msg
    assert "<EMAIL_ADDRESS>" in human_msg
    assert "<PHONE_NUMBER>" in human_msg

@patch("app.core.graph.subgraphs.base_subgraph._llm")
def test_decide_node_resolved(mock_llm):
    subgraph = CertificateSubgraph()
    
    mock_llm_response = MagicMock(spec=AIMessage)
    mock_llm_response.content = json.dumps({
        "resolved": True,
        "needs_clarification": False,
        "escalate": False,
        "draft": "Your certificate has been successfully reissued.",
        "reason": "Successfully verified and reissued course certificate."
    })
    mock_llm.invoke.return_value = mock_llm_response
    
    state = {
        "email": "user@gov.in",
        "message": "Certificate missing",
        "plan": "Check and reissue",
        "tool_results": [{"tool": "reissue_certificate", "summary": "Success"}],
        "retry_count": 0
    }
    
    new_state = subgraph.decide_node(state)
    assert new_state["is_resolved"] is True
    assert new_state["needs_clarification"] is False
    assert new_state["escalated_to_human"] is False
    assert new_state["resolution_draft"] == "Your certificate has been successfully reissued."
    assert new_state["retry_count"] == 1
    assert len(new_state["graph_plan"]) == 1

@patch("app.core.graph.subgraphs.base_subgraph._llm")
def test_decide_node_clarification(mock_llm):
    subgraph = CertificateSubgraph()
    
    mock_llm_response = MagicMock(spec=AIMessage)
    mock_llm_response.content = json.dumps({
        "resolved": False,
        "needs_clarification": True,
        "escalate": False,
        "draft": "Could you please tell me which course you completed?",
        "reason": "Missing course name to check enrollments."
    })
    mock_llm.invoke.return_value = mock_llm_response
    
    state = {
        "email": "user@gov.in",
        "message": "Certificate missing",
        "plan": "Check and reissue",
        "tool_results": [],
        "retry_count": 0
    }
    
    new_state = subgraph.decide_node(state)
    # Clarification/partial-match should treat the iteration as completed (resolved = True in decide_node)
    assert new_state["is_resolved"] is True
    assert new_state["needs_clarification"] is True
    assert new_state["escalated_to_human"] is False
    assert new_state["resolution_draft"] == "Could you please tell me which course you completed?"
    assert new_state["retry_count"] == 1

@patch("app.core.graph.subgraphs.base_subgraph._llm_execute")
def test_execute_node_email_injection(mock_llm_execute):
    subgraph = CertificateSubgraph()
    
    mock_llm_with_tools = MagicMock()
    mock_llm_execute.bind_tools.return_value = mock_llm_with_tools
    
    mock_response = AIMessage(
        content="",
        tool_calls=[{
            "name": "get_user_details",
            "args": {"email": "<EMAIL_ADDRESS>"},
            "id": "call_123"
        }]
    )
    mock_stop_response = AIMessage(content="I have completed execution.")
    mock_llm_with_tools.invoke.side_effect = [mock_response, mock_stop_response]
    
    # Mock tool
    mock_tool = MagicMock()
    mock_tool.name = "get_user_details"
    mock_tool.invoke.return_value = "User is enrolled in multiple courses."
    
    # Override get_tools on subgraph instance
    subgraph.get_tools = MagicMock(return_value=[mock_tool])
    
    state = {
        "email": "real_user@gov.in",
        "message": "Please check my certificate for john.doe@gov.in",
        "plan": "Find user details",
        "tool_results": [],
        "retry_count": 0
    }
    
    new_state = subgraph.execute_node(state)
    
    # Assert tool was called with injected secure email
    mock_tool.invoke.assert_called_once_with({"email": "real_user@gov.in"})
    
    # Assert PII masking was done on user message passed to execute prompt
    called_messages = mock_llm_with_tools.invoke.call_args_list[0][0][0]
    human_msg = called_messages[1].content
    assert "john.doe@gov.in" not in human_msg
    assert "<EMAIL_ADDRESS>" in human_msg
