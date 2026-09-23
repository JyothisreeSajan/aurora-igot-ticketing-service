"""
Unit tests for app/core/graph/subgraphs/base_subgraph.py filling the gaps
NOT already covered by tests/test_subgraphs.py, tests/test_graph_and_subgraphs.py,
tests/test_contact_recovery.py, and tests/test_pii_masking.py:

  - plan_node: LLM-error fallback, continuation/conversation-history branch,
    previous-tool-results + quality-gate-feedback branch
  - execute_node: new_email/new_contact secure recovery, multi-round tool
    loop, unknown-tool handling, tool-exception handling, _spoc_replacements
    merging, the 8-iteration cap, and the outer try/except
  - decide_node: escalate path, plain retry outcome, LLM/JSON error handling
  - should_retry / build() against a minimal concrete subgraph
  - _plan_step's ticket_tracker.add_step wiring

Uses a small concrete BaseSubgraph subclass (BaseSubgraph itself is
abstract) so these tests exercise the base-class contract directly, without
depending on any specific SOP's tools or prompts.
"""

import json
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage

from app.core.graph.subgraphs.base_subgraph import BaseSubgraph


class _DummySubgraph(BaseSubgraph):
    CATEGORY = "dummy_category"

    def __init__(self, tools=None):
        self._tools = tools if tools is not None else []

    def get_tools(self, state):
        return self._tools

    def system_prompt(self, state):
        return "DUMMY SYSTEM PROMPT"


def _ai_message(content):
    resp = MagicMock(spec=AIMessage)
    resp.content = content
    resp.usage_metadata = {}
    return resp


# ── plan_node ────────────────────────────────────────────────────────────────

class TestPlanNode:
    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_llm_error_falls_back_to_kb_search_plan(self, mock_llm):
        subgraph = _DummySubgraph()
        mock_llm.invoke.side_effect = Exception("LLM unavailable")

        state = {"email": "user@gov.in", "message": "help", "retry_count": 0}
        new_state = subgraph.plan_node(state)

        assert "Fallback" in new_state["plan"]

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_continuation_includes_conversation_history(self, mock_llm):
        subgraph = _DummySubgraph()
        mock_llm.invoke.return_value = _ai_message("Plan A")

        state = {
            "email": "user@gov.in",
            "message": "yes that's right",
            "retry_count": 0,
            "is_continuation": True,
            "conversation_messages": [
                {"role": "user", "content": "my email is old@gov.in"},
                {"role": "agent", "content": "Can you confirm the new email?"},
            ],
        }
        subgraph.plan_node(state)

        human_msg = mock_llm.invoke.call_args[0][0][1].content
        assert "CONTINUATION" in human_msg
        assert "[User]" in human_msg and "[Agent]" in human_msg
        # PII in the historical conversation must also be masked
        assert "old@gov.in" not in human_msg

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_previous_results_and_quality_feedback_included(self, mock_llm):
        subgraph = _DummySubgraph()
        mock_llm.invoke.return_value = _ai_message("Plan B")

        state = {
            "email": "user@gov.in",
            "message": "still broken",
            "retry_count": 1,
            "tool_results": [{"tool": "get_user_profile", "summary": "found user"}],
            "quality_gate_feedback": "Draft repeated itself, tighten it up.",
        }
        subgraph.plan_node(state)

        human_msg = mock_llm.invoke.call_args[0][0][1].content
        assert "Previous tool results" in human_msg
        assert "get_user_profile: found user" in human_msg
        assert "QUALITY GATE FEEDBACK" in human_msg
        assert "tighten it up" in human_msg

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_appends_to_existing_graph_plan(self, mock_llm):
        subgraph = _DummySubgraph()
        mock_llm.invoke.return_value = _ai_message("Plan C")

        state = {"email": "user@gov.in", "message": "hi", "retry_count": 0,
                 "graph_plan": [{"node": "prior", "detail": "x"}]}
        new_state = subgraph.plan_node(state)

        assert len(new_state["graph_plan"]) == 2


# ── execute_node ─────────────────────────────────────────────────────────────

class TestExecuteNode:
    @patch("app.core.graph.subgraphs.base_subgraph._recover_new_contact")
    @patch("app.core.graph.subgraphs.base_subgraph._llm_execute")
    def test_new_contact_recovery_overrides_masked_placeholder(self, mock_llm_execute, mock_recover):
        mock_recover.return_value = "real.new@gov.in"
        mock_llm_with_tools = MagicMock()
        mock_llm_execute.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{
                "name": "check_contact_registered",
                "args": {"new_contact": "<EMAIL_ADDRESS>"},
                "id": "tc_1",
            }]),
            AIMessage(content="done"),
        ]
        mock_tool = MagicMock()
        mock_tool.name = "check_contact_registered"
        mock_tool.invoke.return_value = '{"is_registered": false}'

        subgraph = _DummySubgraph(tools=[mock_tool])
        state = {
            "email": "owner@gov.in",
            "message": "please update it to real.new@gov.in",
            "plan": "update contact",
            "tool_results": [],
            "retry_count": 0,
        }

        subgraph.execute_node(state)

        mock_tool.invoke.assert_called_once_with({"new_contact": "real.new@gov.in"})

    @patch("app.core.graph.subgraphs.base_subgraph._llm_execute")
    def test_multi_round_tool_calls_accumulate_results(self, mock_llm_execute):
        mock_llm_with_tools = MagicMock()
        mock_llm_execute.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{"name": "tool_a", "args": {}, "id": "1"}]),
            AIMessage(content="", tool_calls=[{"name": "tool_b", "args": {}, "id": "2"}]),
            AIMessage(content="all done"),
        ]
        tool_a = MagicMock(name="tool_a_mock")
        tool_a.name = "tool_a"
        tool_a.invoke.return_value = "result A"
        tool_b = MagicMock(name="tool_b_mock")
        tool_b.name = "tool_b"
        tool_b.invoke.return_value = "result B"

        subgraph = _DummySubgraph(tools=[tool_a, tool_b])
        state = {"email": "user@gov.in", "message": "msg", "plan": "p", "tool_results": [], "retry_count": 0}

        new_state = subgraph.execute_node(state)

        assert [r["tool"] for r in new_state["tool_results"]] == ["tool_a", "tool_b"]

    @patch("app.core.graph.subgraphs.base_subgraph._llm_execute")
    def test_unknown_tool_name_reports_not_available(self, mock_llm_execute):
        mock_llm_with_tools = MagicMock()
        mock_llm_execute.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{"name": "nonexistent_tool", "args": {}, "id": "1"}]),
            AIMessage(content="done"),
        ]

        subgraph = _DummySubgraph(tools=[])
        state = {"email": "user@gov.in", "message": "msg", "plan": "p", "tool_results": [], "retry_count": 0}

        new_state = subgraph.execute_node(state)

        assert "not available" in new_state["tool_results"][0]["summary"]

    @patch("app.core.graph.subgraphs.base_subgraph._llm_execute")
    def test_tool_exception_is_captured_as_result(self, mock_llm_execute):
        mock_llm_with_tools = MagicMock()
        mock_llm_execute.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{"name": "flaky_tool", "args": {}, "id": "1"}]),
            AIMessage(content="done"),
        ]
        flaky_tool = MagicMock()
        flaky_tool.name = "flaky_tool"
        flaky_tool.invoke.side_effect = Exception("upstream API down")

        subgraph = _DummySubgraph(tools=[flaky_tool])
        state = {"email": "user@gov.in", "message": "msg", "plan": "p", "tool_results": [], "retry_count": 0}

        new_state = subgraph.execute_node(state)

        assert "failed" in new_state["tool_results"][0]["summary"]
        assert "upstream API down" in new_state["tool_results"][0]["summary"]

    @patch("app.core.graph.subgraphs.base_subgraph._llm_execute")
    def test_spoc_replacements_are_extracted_and_stripped(self, mock_llm_execute):
        mock_llm_with_tools = MagicMock()
        mock_llm_execute.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{"name": "get_mdo_details", "args": {}, "id": "1"}]),
            AIMessage(content="done"),
        ]
        mdo_tool = MagicMock()
        mdo_tool.name = "get_mdo_details"
        mdo_tool.invoke.return_value = json.dumps({
            "found": True,
            "_spoc_replacements": {"{{MDO_ADMIN_NAME}}": "Real Admin"},
        })

        subgraph = _DummySubgraph(tools=[mdo_tool])
        state = {"email": "user@gov.in", "message": "msg", "plan": "p", "tool_results": [], "retry_count": 0}

        new_state = subgraph.execute_node(state)

        assert new_state["spoc_replacements"]["{{MDO_ADMIN_NAME}}"] == "Real Admin"
        assert "_spoc_replacements" not in new_state["tool_results"][0]["summary"]

    @patch("app.core.graph.subgraphs.base_subgraph._llm_execute")
    def test_spoc_replacements_accumulate_across_calls(self, mock_llm_execute):
        mock_llm_with_tools = MagicMock()
        mock_llm_execute.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{"name": "tool_a", "args": {}, "id": "1"}]),
            AIMessage(content="done"),
        ]
        tool_a = MagicMock()
        tool_a.name = "tool_a"
        tool_a.invoke.return_value = json.dumps({"_spoc_replacements": {"{{NEW_KEY}}": "new value"}})

        subgraph = _DummySubgraph(tools=[tool_a])
        state = {
            "email": "user@gov.in", "message": "msg", "plan": "p", "tool_results": [], "retry_count": 0,
            "spoc_replacements": {"{{EXISTING_KEY}}": "existing value"},
        }

        new_state = subgraph.execute_node(state)

        assert new_state["spoc_replacements"] == {
            "{{EXISTING_KEY}}": "existing value",
            "{{NEW_KEY}}": "new value",
        }

    @patch("app.core.graph.subgraphs.base_subgraph._llm_execute")
    def test_loop_stops_after_eight_rounds(self, mock_llm_execute):
        mock_llm_with_tools = MagicMock()
        mock_llm_execute.bind_tools.return_value = mock_llm_with_tools
        # Every round returns a new tool call, never stopping on its own —
        # the hard cap of 8 iterations in execute_node must still terminate it.
        mock_llm_with_tools.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{"name": "loopy_tool", "args": {}, "id": str(i)}])
            for i in range(8)
        ]
        loopy_tool = MagicMock()
        loopy_tool.name = "loopy_tool"
        loopy_tool.invoke.return_value = "ok"

        subgraph = _DummySubgraph(tools=[loopy_tool])
        state = {"email": "user@gov.in", "message": "msg", "plan": "p", "tool_results": [], "retry_count": 0}

        new_state = subgraph.execute_node(state)

        assert len(new_state["tool_results"]) == 8
        assert mock_llm_with_tools.invoke.call_count == 8

    @patch("app.core.graph.subgraphs.base_subgraph._llm_execute")
    def test_malformed_spoc_replacements_json_is_swallowed(self, mock_llm_execute):
        mock_llm_with_tools = MagicMock()
        mock_llm_execute.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{"name": "broken_tool", "args": {}, "id": "1"}]),
            AIMessage(content="done"),
        ]
        broken_tool = MagicMock()
        broken_tool.name = "broken_tool"
        # Contains the substring "_spoc_replacements" but isn't valid JSON —
        # exercises the inner try/except around json.loads(result_str).
        broken_tool.invoke.return_value = "not json but mentions _spoc_replacements anyway"

        subgraph = _DummySubgraph(tools=[broken_tool])
        state = {"email": "user@gov.in", "message": "msg", "plan": "p", "tool_results": [], "retry_count": 0}

        new_state = subgraph.execute_node(state)

        assert new_state["tool_results"][0]["summary"] == "not json but mentions _spoc_replacements anyway"

    @patch("app.core.graph.subgraphs.base_subgraph._llm_execute")
    def test_llm_exception_is_handled_and_preserves_prior_results(self, mock_llm_execute):
        mock_llm_with_tools = MagicMock()
        mock_llm_execute.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.invoke.side_effect = Exception("model timeout")

        subgraph = _DummySubgraph(tools=[])
        state = {
            "email": "user@gov.in", "message": "msg", "plan": "p",
            "tool_results": [{"tool": "prior_tool", "args": {}, "summary": "prior"}],
            "retry_count": 0,
        }

        new_state = subgraph.execute_node(state)

        assert new_state["tool_results"] == [{"tool": "prior_tool", "args": {}, "summary": "prior"}]
        assert len(new_state["graph_plan"]) == 1


# ── decide_node ──────────────────────────────────────────────────────────────

class TestDecideNode:
    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_escalate_path(self, mock_llm):
        mock_llm.invoke.return_value = _ai_message(json.dumps({
            "resolved": False,
            "needs_clarification": False,
            "escalate": True,
            "draft": "Escalating to human agent.",
            "reason": "Outside SOP scope.",
        }))
        subgraph = _DummySubgraph()
        state = {"email": "user@gov.in", "message": "complex issue", "plan": "p", "tool_results": [], "retry_count": 0}

        new_state = subgraph.decide_node(state)

        assert new_state["is_resolved"] is True
        assert new_state["escalated_to_human"] is True
        assert new_state["resolution_draft"] == "Escalating to human agent."

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_retry_outcome_when_not_resolved(self, mock_llm):
        mock_llm.invoke.return_value = _ai_message(json.dumps({
            "resolved": False,
            "needs_clarification": False,
            "escalate": False,
            "draft": "",
            "reason": "Need to fetch more data first.",
        }))
        subgraph = _DummySubgraph()
        state = {"email": "user@gov.in", "message": "issue", "plan": "p", "tool_results": [], "retry_count": 0}

        new_state = subgraph.decide_node(state)

        assert new_state["is_resolved"] is False
        assert new_state["escalated_to_human"] is False
        assert new_state["needs_clarification"] is False
        assert new_state["graph_plan"][0]["outcome"] == "retry"

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_llm_exception_defaults_to_retry(self, mock_llm):
        mock_llm.invoke.side_effect = Exception("model down")
        subgraph = _DummySubgraph()
        state = {"email": "user@gov.in", "message": "issue", "plan": "p", "tool_results": [], "retry_count": 0}

        new_state = subgraph.decide_node(state)

        assert new_state["is_resolved"] is False
        assert new_state["graph_plan"][0]["outcome"] == "retry"

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_malformed_json_defaults_to_retry(self, mock_llm):
        mock_llm.invoke.return_value = _ai_message("not valid json at all")
        subgraph = _DummySubgraph()
        state = {"email": "user@gov.in", "message": "issue", "plan": "p", "tool_results": [], "retry_count": 0}

        new_state = subgraph.decide_node(state)

        assert new_state["is_resolved"] is False

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_quality_gate_feedback_appended_to_plan(self, mock_llm):
        mock_llm.invoke.return_value = _ai_message(json.dumps({
            "resolved": True, "needs_clarification": False, "escalate": False,
            "draft": "Final answer.", "reason": "ok",
        }))
        subgraph = _DummySubgraph()
        state = {
            "email": "user@gov.in", "message": "issue", "plan": "Base plan",
            "tool_results": [], "retry_count": 0,
            "quality_gate_feedback": "Avoid repeating the same sentence twice.",
        }

        subgraph.decide_node(state)

        human_msg = mock_llm.invoke.call_args[0][0][1].content
        assert "QUALITY GATE FEEDBACK" in human_msg
        assert "Avoid repeating the same sentence twice." in human_msg

    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_not_resolved_preserves_prior_resolution_draft(self, mock_llm):
        mock_llm.invoke.return_value = _ai_message(json.dumps({
            "resolved": False, "needs_clarification": False, "escalate": False,
            "draft": "", "reason": "still working",
        }))
        subgraph = _DummySubgraph()
        state = {
            "email": "user@gov.in", "message": "issue", "plan": "p", "tool_results": [], "retry_count": 0,
            "resolution_draft": "previous draft text",
        }

        new_state = subgraph.decide_node(state)

        assert new_state["resolution_draft"] == "previous draft text"


# ── should_retry / build ─────────────────────────────────────────────────────

class TestShouldRetryAndBuild:
    def test_resolved_returns_done(self):
        subgraph = _DummySubgraph()
        assert subgraph.should_retry({"is_resolved": True, "retry_count": 0, "max_retries": 3}) == "done"

    def test_under_max_retries_returns_plan_node(self):
        subgraph = _DummySubgraph()
        assert subgraph.should_retry({"is_resolved": False, "retry_count": 1, "max_retries": 3}) == "plan_node"

    def test_max_retries_reached_returns_done(self):
        subgraph = _DummySubgraph()
        assert subgraph.should_retry({"is_resolved": False, "retry_count": 3, "max_retries": 3}) == "done"

    def test_build_compiles_runnable_graph(self):
        subgraph = _DummySubgraph()
        compiled = subgraph.build()

        assert hasattr(compiled, "invoke")
        assert hasattr(compiled, "ainvoke")


# ── _plan_step / ticket_tracker wiring ───────────────────────────────────────

class TestPlanStepTicketTracking:
    @patch("app.core.graph.subgraphs.base_subgraph.ticket_tracker")
    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_real_ticket_id_records_step_in_tracker(self, mock_llm, mock_tracker):
        mock_llm.invoke.return_value = _ai_message("Plan D")
        subgraph = _DummySubgraph()

        state = {"ticket_id": "TKT-123", "email": "user@gov.in", "message": "hi", "retry_count": 0}
        subgraph.plan_node(state)

        mock_tracker.add_step.assert_called_once()
        args = mock_tracker.add_step.call_args[0]
        assert args[0] == "TKT-123"
        assert args[1] == "dummy_category/plan_node"

    @patch("app.core.graph.subgraphs.base_subgraph.ticket_tracker")
    @patch("app.core.graph.subgraphs.base_subgraph._llm")
    def test_unknown_ticket_id_skips_tracker(self, mock_llm, mock_tracker):
        mock_llm.invoke.return_value = _ai_message("Plan E")
        subgraph = _DummySubgraph()

        state = {"email": "user@gov.in", "message": "hi", "retry_count": 0}
        subgraph.plan_node(state)

        mock_tracker.add_step.assert_not_called()
