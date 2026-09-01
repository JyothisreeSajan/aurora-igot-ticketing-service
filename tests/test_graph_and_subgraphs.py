"""
tests/test_graph_and_subgraphs.py
----------------------------------
End-to-end tests for the Main Resolution Graph flow (arun_ticket)
and Subgraph execution loops (plan_node -> execute_node -> decide_node).
"""

from unittest.mock import MagicMock, patch, AsyncMock
import pytest
from langchain_core.messages import AIMessage

from app.core.graph.main_graph import arun_ticket
from app.core.graph.subgraphs.ca_apar_subgraph import CaAparSubgraph
from app.core.graph.subgraphs.content_related_subgraph import ContentRelatedSubgraph
from app.core.graph.subgraphs.profile_user_management_subgraph import ProfileUserManagementSubgraph


# ── 1. Main Graph Flow Tests (arun_ticket) ───────────────────────────────────

@pytest.mark.anyio
@patch("app.core.graph.nodes.intake_node.VALIDATE_EMAIL", True)
@patch("app.core.graph.nodes.intake_node.ENABLED_CATEGORIES", ["*"])
@patch("app.core.graph.nodes.intake_node._llm")
@patch("app.core.graph.subgraphs.base_subgraph._llm")
@patch("app.core.graph.subgraphs.base_subgraph._llm_execute")
@patch("app.core.graph.main_graph._llm_quality")
@patch("app.core.graph.nodes.intake_node.fetch_user_info")
@patch("app.core.graph.nodes.intake_node.is_domain_allowed")
async def test_main_graph_full_flow_success(
    mock_domain, mock_user_info, mock_quality, mock_exec, mock_sub_llm, mock_intake_llm
):
    """Test full main graph flow from intake to router, ca_apar subgraph, quality gate, and output."""
    mock_domain.return_value = True
    mock_user_info.return_value = (True, "Rakesh")

    # Intake node: classification response
    intake_resp = MagicMock()
    intake_resp.content = '{"is_junk": false, "category": "ca_apar_issue", "main_category": "ca_apar_issue", "sub_category": "apar_training_plan_not_visible", "confidence": 0.95}'
    intake_resp.usage_metadata = {}
    mock_intake_llm.invoke.return_value = intake_resp

    # Subgraph plan node response
    plan_resp = MagicMock(spec=AIMessage)
    plan_resp.content = "Plan: 1. Check user APAR training plan."
    plan_resp.usage_metadata = {}

    # Subgraph decide node response
    decide_resp = MagicMock(spec=AIMessage)
    decide_resp.content = '{"resolved": true, "needs_clarification": false, "escalate": false, "draft": "Your APAR training plan is visible on profile.", "reason": "SOP verified"}'
    decide_resp.usage_metadata = {}

    mock_sub_llm.invoke.side_effect = [plan_resp, decide_resp]

    # Subgraph execute node (no tool calls -> stop)
    exec_tools = MagicMock()
    exec_resp = AIMessage(content="CBP plan retrieved successfully.")
    exec_resp.usage_metadata = {}
    exec_resp.tool_calls = []
    exec_tools.invoke.return_value = exec_resp
    mock_exec.bind_tools.return_value = exec_tools

    # Quality gate response
    quality_resp = MagicMock()
    quality_resp.content = '{"has_repetition": false}'
    mock_quality.invoke.return_value = quality_resp

    ticket_dict = {
        "ticket_id": "t_main_flow_001",
        "email": "rakesh@gov.in",
        "message": "APAR training plan not visible on my profile",
        "channel": "email"
    }

    result = await arun_ticket(ticket_dict)

    assert result["ticket_id"] == "t_main_flow_001"
    assert result["main_category"] == "ca_apar_issue"
    assert result["route_to"] == "ca_apar_subgraph"
    assert result["final_response"] is not None
    assert "Hi Rakesh," in result["final_response"]


@pytest.mark.anyio
@patch("app.core.graph.nodes.intake_node.detect_junk")
async def test_main_graph_junk_early_exit(mock_detect_junk):
    """Test that junk messages exit early at intake and do not run subgraphs."""
    mock_detect_junk.return_value = (True, 0.99, "Greeting only")

    ticket_dict = {
        "ticket_id": "t_junk_001",
        "email": "user@gov.in",
        "message": "Good morning",
        "channel": "email"
    }

    result = await arun_ticket(ticket_dict)

    assert result["is_junk"] is True
    assert result["is_resolved"] is True
    assert result["final_response"] is not None


# ── 2. Subgraph Execution Loop Tests (Plan -> Execute -> Decide) ─────────────

@patch("app.core.graph.subgraphs.base_subgraph._llm")
def test_ca_apar_subgraph_plan_node(mock_llm):
    """Test CaAparSubgraph plan_node SOP plan generation."""
    subgraph = CaAparSubgraph()

    mock_resp = MagicMock(spec=AIMessage)
    mock_resp.content = "Plan: Verify user APAR training plan."
    mock_resp.usage_metadata = {}
    mock_llm.invoke.return_value = mock_resp

    state = {
        "ticket_id": "t_sub_plan_001",
        "email": "official@gov.in",
        "message": "APAR issue for profile",
        "main_category": "ca_apar_issue",
        "retry_count": 0,
        "enriched_context": {"kb_snippets": []}
    }

    new_state = subgraph.plan_node(state)
    assert new_state["plan"] == "Plan: Verify user APAR training plan."
    assert len(new_state["graph_plan"]) == 1


@patch("app.core.graph.subgraphs.base_subgraph._llm_execute")
def test_subgraph_execute_node_tool_invocation(mock_llm_execute):
    """Test execute_node tools execution and user email injection."""
    subgraph = ContentRelatedSubgraph()

    mock_llm_with_tools = MagicMock()
    mock_llm_execute.bind_tools.return_value = mock_llm_with_tools

    tool_call_response = AIMessage(
        content="",
        tool_calls=[{
            "name": "get_user_profile",
            "args": {"email": "injected@gov.in"},
            "id": "tc_123"
        }]
    )
    stop_response = AIMessage(content="Course details retrieved successfully.")
    mock_llm_with_tools.invoke.side_effect = [tool_call_response, stop_response]

    # Mock tool
    mock_tool = MagicMock()
    mock_tool.name = "get_user_profile"
    mock_tool.invoke.return_value = '{"firstName": "Sandhya", "orgName": "DPIIT"}'

    subgraph.get_tools = MagicMock(return_value=[mock_tool])

    state = {
        "ticket_id": "t_exec_001",
        "email": "sandhya@gov.in",
        "message": "Check my course access",
        "plan": "Lookup profile",
        "tool_results": [],
        "retry_count": 0
    }

    new_state = subgraph.execute_node(state)

    # Secure user email injection assertion
    mock_tool.invoke.assert_called_once_with({"email": "sandhya@gov.in"})
    assert len(new_state["tool_results"]) == 1
    assert new_state["tool_results"][0]["tool"] == "get_user_profile"


@patch("app.core.graph.subgraphs.base_subgraph._llm")
def test_subgraph_decide_node_resolution(mock_llm):
    """Test decide_node verdict resolution."""
    subgraph = ProfileUserManagementSubgraph()

    mock_resp = MagicMock()
    mock_resp.content = '{"resolved": true, "needs_clarification": false, "escalate": false, "draft": "Please reset password via official portal.", "reason": "SOP followed"}'
    mock_resp.usage_metadata = {}
    mock_llm.invoke.return_value = mock_resp

    state = {
        "ticket_id": "t_decide_001",
        "email": "user@gov.in",
        "message": "Unable to login",
        "plan": "Guide password reset",
        "tool_results": [],
        "retry_count": 0
    }

    new_state = subgraph.decide_node(state)
    assert new_state["is_resolved"] is True
    assert new_state["resolution_draft"] == "Please reset password via official portal."
