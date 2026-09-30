"""
Unit tests for app/core/graph/subgraphs/content_related_subgraph.py —
the dispatch between the implemented "enrolment_issues" and
"course_program_progress_issue" sub-categories (their own tools/prompt
modules) and every other, still-stubbed, content_related_issue sub-category
(stub_tools / STUB_SUBGRAPH_SYSTEM_PROMPT), plus the greeting-name
extraction this subgraph adds on top of BaseSubgraph (same pattern as
ProfileUserManagementSubgraph / RecognitionEngagementSubgraph).
"""

import json
from unittest.mock import patch

from app.core.graph.subgraphs.content_related_subgraph import ContentRelatedSubgraph
from app.core.tools.certificate_tools import get_certificate_not_received_tools
from app.core.tools.course_progress_tools import get_course_progress_tools
from app.core.tools.enrolment_tools import get_enrolment_tools
from app.core.tools.event_tools import get_event_tools
from app.core.tools.stub_tools import get_stub_tools
from app.core.utils.prompt_templates import (
    CERTIFICATE_NAME_ISSUE_SYSTEM_PROMPT,
    CERTIFICATE_NOT_RECEIVED_SYSTEM_PROMPT,
    COURSE_PROGRESS_SYSTEM_PROMPT,
    ENROLMENT_ISSUES_SYSTEM_PROMPT,
    EVENT_ISSUES_SYSTEM_PROMPT,
    RATING_FEEDBACK_ISSUE_SYSTEM_PROMPT,
    STUB_SUBGRAPH_SYSTEM_PROMPT,
)


def _tool_result(tool, summary_dict):
    return {"tool": tool, "summary": json.dumps(summary_dict)}


# ── _is_implemented ──────────────────────────────────────────────────────────

class TestIsImplemented:
    def test_enrolment_issues_is_implemented(self):
        subgraph = ContentRelatedSubgraph()

        assert subgraph._is_implemented({"sub_category": "enrolment_issues"}) is True

    def test_course_program_progress_issue_is_implemented(self):
        subgraph = ContentRelatedSubgraph()

        assert subgraph._is_implemented({"sub_category": "course_program_progress_issue"}) is True

    def test_rating_feedback_issue_is_implemented(self):
        subgraph = ContentRelatedSubgraph()

        assert subgraph._is_implemented({"sub_category": "unable_to_submit_rating_feedback"}) is True

    def test_certificate_issue_is_implemented(self):
        subgraph = ContentRelatedSubgraph()

        assert subgraph._is_implemented({"sub_category": "certificate_issue"}) is True

    def test_certificate_not_received_is_implemented(self):
        subgraph = ContentRelatedSubgraph()

        assert subgraph._is_implemented({"sub_category": "certificate_not_received"}) is True

    def test_other_sub_categories_are_stubbed(self):
        subgraph = ContentRelatedSubgraph()

        for sub_category in [
            "content_resource_not_opening",
            "",
        ]:
            assert subgraph._is_implemented({"sub_category": sub_category}) is False

    def test_missing_sub_category_is_stubbed(self):
        subgraph = ContentRelatedSubgraph()

        assert subgraph._is_implemented({}) is False


# ── system_prompt ─────────────────────────────────────────────────────────────

class TestSystemPrompt:
    def test_enrolment_issues_uses_dedicated_prompt(self):
        subgraph = ContentRelatedSubgraph()
        state = {"sub_category": "enrolment_issues", "main_category": "content_related_issue"}

        prompt = subgraph.system_prompt(state)

        assert prompt == ENROLMENT_ISSUES_SYSTEM_PROMPT.format(
            email="unknown", main_category="content_related_issue"
        )

    def test_course_program_progress_issue_uses_dedicated_prompt(self):
        subgraph = ContentRelatedSubgraph()
        state = {"sub_category": "course_program_progress_issue", "main_category": "content_related_issue"}

        prompt = subgraph.system_prompt(state)

        assert prompt == COURSE_PROGRESS_SYSTEM_PROMPT.format(
            email="unknown", main_category="content_related_issue"
        )

    def test_rating_feedback_issue_uses_dedicated_prompt(self):
        subgraph = ContentRelatedSubgraph()
        state = {"sub_category": "unable_to_submit_rating_feedback", "main_category": "content_related_issue"}

        prompt = subgraph.system_prompt(state)

        assert prompt == RATING_FEEDBACK_ISSUE_SYSTEM_PROMPT.format(
            email="unknown", main_category="content_related_issue"
        )

    def test_certificate_issue_uses_dedicated_prompt(self):
        subgraph = ContentRelatedSubgraph()
        state = {"sub_category": "certificate_issue", "main_category": "content_related_issue"}

        prompt = subgraph.system_prompt(state)

        assert prompt == CERTIFICATE_NAME_ISSUE_SYSTEM_PROMPT.format(
            email="unknown", main_category="content_related_issue"
        )

    def test_certificate_not_received_uses_dedicated_prompt(self):
        subgraph = ContentRelatedSubgraph()
        state = {"sub_category": "certificate_not_received", "main_category": "content_related_issue"}

        prompt = subgraph.system_prompt(state)

        assert prompt == CERTIFICATE_NOT_RECEIVED_SYSTEM_PROMPT.format(
            email="unknown", main_category="content_related_issue"
        )

    def test_other_sub_category_uses_stub_prompt(self):
        subgraph = ContentRelatedSubgraph()
        state = {"sub_category": "content_resource_not_opening", "main_category": "content_related_issue"}

        prompt = subgraph.system_prompt(state)

        assert prompt == STUB_SUBGRAPH_SYSTEM_PROMPT.format(
            email="unknown", main_category="content_related_issue"
        )

    def test_missing_state_fields_default(self):
        subgraph = ContentRelatedSubgraph()

        prompt = subgraph.system_prompt({"sub_category": "enrolment_issues"})

        assert prompt == ENROLMENT_ISSUES_SYSTEM_PROMPT.format(
            email="unknown", main_category="content_related_issue"
        )


# ── get_tools ─────────────────────────────────────────────────────────────────

class TestGetTools:
    def test_enrolment_issues_returns_enrolment_tools(self):
        subgraph = ContentRelatedSubgraph()

        tools = subgraph.get_tools({"sub_category": "enrolment_issues"})

        assert {t.name for t in tools} == {t.name for t in get_enrolment_tools()}

    def test_course_program_progress_issue_returns_course_progress_tools(self):
        subgraph = ContentRelatedSubgraph()

        tools = subgraph.get_tools({"sub_category": "course_program_progress_issue"})

        assert {t.name for t in tools} == {t.name for t in get_course_progress_tools()}

    def test_rating_feedback_issue_returns_no_tools(self):
        subgraph = ContentRelatedSubgraph()

        tools = subgraph.get_tools({"sub_category": "unable_to_submit_rating_feedback"})

        assert tools == []

    def test_certificate_issue_returns_get_user_details_only(self):
        subgraph = ContentRelatedSubgraph()

        tools = subgraph.get_tools({"sub_category": "certificate_issue"})

        assert [t.name for t in tools] == ["get_user_details"]

    def test_certificate_not_received_returns_certificate_not_received_tools(self):
        subgraph = ContentRelatedSubgraph()

        tools = subgraph.get_tools({"sub_category": "certificate_not_received"})

        assert {t.name for t in tools} == {t.name for t in get_certificate_not_received_tools()}

    def test_other_sub_category_returns_stub_tools(self):
        subgraph = ContentRelatedSubgraph()

        tools = subgraph.get_tools({"sub_category": "content_resource_not_opening"})

        assert tools == get_stub_tools()

    def test_missing_sub_category_returns_stub_tools(self):
        subgraph = ContentRelatedSubgraph()

        tools = subgraph.get_tools({})

        assert tools == get_stub_tools()


# ── _extract_first_name / execute_node ──────────────────────────────────────

class TestExtractFirstName:
    def test_found_via_eligibility_profile(self):
        subgraph = ContentRelatedSubgraph()
        tool_results = [_tool_result("get_user_eligibility_profile", {"found": True, "first_name": "Meera"})]

        assert subgraph._extract_first_name(tool_results) == "Meera"

    def test_found_via_get_user_details_camel_case(self):
        subgraph = ContentRelatedSubgraph()
        tool_results = [_tool_result("get_user_details", {"firstName": "Bharath"})]

        assert subgraph._extract_first_name(tool_results) == "Bharath"

    def test_ignores_unrelated_tools(self):
        subgraph = ContentRelatedSubgraph()
        tool_results = [_tool_result("search_course_or_program", {"first_name": "Should Not Match"})]

        assert subgraph._extract_first_name(tool_results) is None

    def test_no_tool_results(self):
        subgraph = ContentRelatedSubgraph()

        assert subgraph._extract_first_name([]) is None

    def test_malformed_json_is_skipped_not_raised(self):
        subgraph = ContentRelatedSubgraph()
        tool_results = [{"tool": "get_user_eligibility_profile", "summary": "not valid json"}]

        assert subgraph._extract_first_name(tool_results) is None

    def test_found_false_with_no_first_name(self):
        subgraph = ContentRelatedSubgraph()
        tool_results = [_tool_result("get_user_eligibility_profile", {"found": False})]

        assert subgraph._extract_first_name(tool_results) is None

    def test_uses_last_matching_result(self):
        subgraph = ContentRelatedSubgraph()
        tool_results = [
            _tool_result("get_user_eligibility_profile", {"first_name": "First"}),
            _tool_result("get_user_eligibility_profile", {"first_name": "Second"}),
        ]

        assert subgraph._extract_first_name(tool_results) == "Second"


class TestExecuteNode:
    @patch("app.core.graph.subgraphs.base_subgraph.BaseSubgraph.execute_node")
    def test_merges_first_name_when_found(self, mock_super_execute):
        mock_super_execute.return_value = {
            "tool_results": [_tool_result("get_user_eligibility_profile", {"first_name": "Meera"})],
        }
        subgraph = ContentRelatedSubgraph()

        result = subgraph.execute_node({"ticket_id": "t1"})

        assert result["user_first_name"] == "Meera"

    @patch("app.core.graph.subgraphs.base_subgraph.BaseSubgraph.execute_node")
    def test_no_key_added_when_name_not_found(self, mock_super_execute):
        mock_super_execute.return_value = {"tool_results": []}
        subgraph = ContentRelatedSubgraph()

        result = subgraph.execute_node({"ticket_id": "t1"})

        assert "user_first_name" not in result


# ── event_related_issue ──────────────────────────────────────────────────────

class TestEventRelatedIssue:
    def test_is_implemented(self):
        assert ContentRelatedSubgraph()._is_implemented({"sub_category": "event_related_issue"}) is True

    def test_uses_dedicated_prompt(self):
        state = {"sub_category": "event_related_issue", "main_category": "content_related_issue"}

        prompt = ContentRelatedSubgraph().system_prompt(state)

        assert prompt == EVENT_ISSUES_SYSTEM_PROMPT.format(
            email="unknown", main_category="content_related_issue"
        )

    def test_uses_event_tools(self):
        tools = ContentRelatedSubgraph().get_tools({"sub_category": "event_related_issue"})

        assert tools == get_event_tools()
