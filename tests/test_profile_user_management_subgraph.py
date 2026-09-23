"""
Unit tests for app/core/graph/subgraphs/profile_user_management_subgraph.py —
specifically the logic this subgraph overrides on top of BaseSubgraph (covered
generically, without SOP-specific content, by test_base_subgraph.py and
test_graph_and_subgraphs.py):

  _extract_first_name / execute_node  — greeting-name extraction from
                                         get_user_transfer_request_details /
                                         get_user_profile tool results
  decide_node                         — structural dead-end detection that
                                         routes a genuine "nothing left to
                                         automate" case to a silent
                                         human_queue hand-off (empty draft,
                                         confidence=0.0) instead of a normal
                                         escalate email
"""

import json
from unittest.mock import MagicMock, patch

from app.core.graph.subgraphs.profile_user_management_subgraph import (
    ProfileUserManagementSubgraph,
)


def _tool_result(tool, summary_dict):
    return {"tool": tool, "summary": json.dumps(summary_dict)}


# ── _extract_first_name / execute_node ──────────────────────────────────────

class TestExtractFirstName:
    def test_found_via_transfer_request_details(self):
        subgraph = ProfileUserManagementSubgraph()
        tool_results = [_tool_result("get_user_transfer_request_details", {"firstName": "Asha"})]

        assert subgraph._extract_first_name(tool_results) == "Asha"

    def test_found_via_user_profile_snake_case(self):
        subgraph = ProfileUserManagementSubgraph()
        tool_results = [_tool_result("get_user_profile", {"first_name": "Ravi"})]

        assert subgraph._extract_first_name(tool_results) == "Ravi"

    def test_ignores_unrelated_tools(self):
        subgraph = ProfileUserManagementSubgraph()
        tool_results = [_tool_result("get_mdo_details_by_org_id", {"firstName": "Should Not Match"})]

        assert subgraph._extract_first_name(tool_results) is None

    def test_no_tool_results(self):
        subgraph = ProfileUserManagementSubgraph()

        assert subgraph._extract_first_name([]) is None

    def test_malformed_json_is_skipped_not_raised(self):
        subgraph = ProfileUserManagementSubgraph()
        tool_results = [{"tool": "get_user_profile", "summary": "not valid json"}]

        assert subgraph._extract_first_name(tool_results) is None

    def test_uses_last_matching_result(self):
        subgraph = ProfileUserManagementSubgraph()
        tool_results = [
            _tool_result("get_user_transfer_request_details", {"firstName": "First"}),
            _tool_result("get_user_profile", {"firstName": "Second"}),
        ]

        assert subgraph._extract_first_name(tool_results) == "Second"


class TestExecuteNode:
    @patch("app.core.graph.subgraphs.base_subgraph.BaseSubgraph.execute_node")
    def test_merges_first_name_when_found(self, mock_super_execute):
        mock_super_execute.return_value = {
            "tool_results": [_tool_result("get_user_profile", {"firstName": "Asha"})],
        }
        subgraph = ProfileUserManagementSubgraph()

        result = subgraph.execute_node({"ticket_id": "t1"})

        assert result["user_first_name"] == "Asha"

    @patch("app.core.graph.subgraphs.base_subgraph.BaseSubgraph.execute_node")
    def test_no_key_added_when_name_not_found(self, mock_super_execute):
        mock_super_execute.return_value = {"tool_results": []}
        subgraph = ProfileUserManagementSubgraph()

        result = subgraph.execute_node({"ticket_id": "t1"})

        assert "user_first_name" not in result


# ── decide_node — structural dead-end detection ─────────────────────────────

def _mock_llm_escalate(mock_llm, reason="escalating"):
    mock_resp = MagicMock()
    mock_resp.content = json.dumps({
        "resolved": True,
        "needs_clarification": False,
        "escalate": True,
        "draft": "We could not resolve this automatically.",
        "reason": reason,
    })
    mock_resp.usage_metadata = {}
    mock_llm.invoke.return_value = mock_resp


def _mock_llm_resolved(mock_llm):
    mock_resp = MagicMock()
    mock_resp.content = json.dumps({
        "resolved": True,
        "needs_clarification": False,
        "escalate": False,
        "draft": "Here are the steps to update your profile.",
        "reason": "SOP followed",
    })
    mock_resp.usage_metadata = {}
    mock_llm.invoke.return_value = mock_resp


class TestDecideNodeDeadEndDetection:
    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_not_escalated_passes_through_unchanged(self, mock_llm):
        _mock_llm_resolved(mock_llm)
        subgraph = ProfileUserManagementSubgraph()
        state = {"ticket_id": "t1", "email": "u@gov.in", "message": "msg", "tool_results": [], "retry_count": 0}

        result = subgraph.decide_node(state)

        assert result["escalated_to_human"] is False
        assert result["resolution_draft"] == "Here are the steps to update your profile."
        assert "escalation_reason" not in result

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_access_revoked_dead_end_no_ehrms_tool(self, mock_llm):
        """get_yp_am_details fails, no get_user_ehrms_details call -> Access Revoked reason."""
        _mock_llm_escalate(mock_llm)
        subgraph = ProfileUserManagementSubgraph()
        tool_results = [_tool_result("get_yp_am_details", {"found": False})]
        state = {
            "ticket_id": "t1", "email": "u@gov.in", "message": "msg",
            "tool_results": tool_results, "retry_count": 0,
        }

        result = subgraph.decide_node(state)

        assert result["resolution_draft"] == ""
        assert result["confidence"] == 0.0
        assert "Access Revoked" in result["escalation_reason"]

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_date_of_retirement_dead_end_with_ehrms_tool(self, mock_llm):
        """Same get_yp_am_details failure, but get_user_ehrms_details WAS also
        called this turn -> Date of Retirement reason instead."""
        _mock_llm_escalate(mock_llm)
        subgraph = ProfileUserManagementSubgraph()
        tool_results = [
            _tool_result("get_user_ehrms_details", {"found": True, "ehrms_id_set": False}),
            _tool_result("get_yp_am_details", {"found": False}),
        ]
        state = {
            "ticket_id": "t1", "email": "u@gov.in", "message": "msg",
            "tool_results": tool_results, "retry_count": 0,
        }

        result = subgraph.decide_node(state)

        assert result["resolution_draft"] == ""
        assert result["confidence"] == 0.0
        assert "Date of Retirement" in result["escalation_reason"]

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_mother_tongue_dead_end(self, mock_llm):
        _mock_llm_escalate(mock_llm)
        subgraph = ProfileUserManagementSubgraph()
        tool_results = [_tool_result("check_mother_tongue_available", {"found": False})]
        state = {
            "ticket_id": "t1", "email": "u@gov.in", "message": "msg",
            "tool_results": tool_results, "retry_count": 0,
        }

        result = subgraph.decide_node(state)

        assert result["resolution_draft"] == ""
        assert result["confidence"] == 0.0
        assert "Mother Tongue" in result["escalation_reason"]

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_no_mdo_admin_dead_end(self, mock_llm):
        """get_mdo_details_by_org_id fails, no get_org_imported_designations call
        (i.e. NOT the Designation Not Found flow) -> generic MDO-not-found reason."""
        _mock_llm_escalate(mock_llm)
        subgraph = ProfileUserManagementSubgraph()
        tool_results = [_tool_result("get_mdo_details_by_org_id", {"found": False})]
        state = {
            "ticket_id": "t1", "email": "u@gov.in", "message": "msg",
            "tool_results": tool_results, "retry_count": 0,
        }

        result = subgraph.decide_node(state)

        assert result["resolution_draft"] == ""
        assert result["confidence"] == 0.0
        assert "No MDO Admin found" in result["escalation_reason"]

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_designation_not_found_mdo_failure_is_not_silently_dropped(self, mock_llm):
        """SOP-P3 Case 2 (Designation Not Found) ALSO calls
        get_org_imported_designations, which must exempt it from the generic
        silent hand-off — its 'MDO not found' should still be a normal,
        customer-facing escalate email, not an emptied draft."""
        _mock_llm_escalate(mock_llm, reason="Designation not imported, no MDO found")
        subgraph = ProfileUserManagementSubgraph()
        tool_results = [
            _tool_result("get_org_imported_designations", {"found": True, "imported_designations": []}),
            _tool_result("get_mdo_details_by_org_id", {"found": False}),
        ]
        state = {
            "ticket_id": "t1", "email": "u@gov.in", "message": "msg",
            "tool_results": tool_results, "retry_count": 0,
        }

        result = subgraph.decide_node(state)

        assert result["resolution_draft"] == "We could not resolve this automatically."
        assert "escalation_reason" not in result

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_successful_lookup_does_not_trigger_dead_end(self, mock_llm):
        """found=true for the same tools must NOT be mistaken for a dead end."""
        _mock_llm_resolved(mock_llm)
        subgraph = ProfileUserManagementSubgraph()
        tool_results = [_tool_result("get_yp_am_details", {"found": True})]
        state = {
            "ticket_id": "t1", "email": "u@gov.in", "message": "msg",
            "tool_results": tool_results, "retry_count": 0,
        }

        result = subgraph.decide_node(state)

        assert "escalation_reason" not in result
        assert result["resolution_draft"] != ""
