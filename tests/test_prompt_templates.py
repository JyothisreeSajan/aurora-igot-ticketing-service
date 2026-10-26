"""Tests for escalation-reason wording rules in prompt_templates."""
from app.core.utils.prompt_templates import BASE_DECIDE_HUMAN_MESSAGE


def test_escalation_reason_is_written_for_support_agents():
    assert "written for the human support agent" in BASE_DECIDE_HUMAN_MESSAGE


def test_escalation_reason_forbids_tool_and_api_names():
    assert "never mention tool, function or API names" in BASE_DECIDE_HUMAN_MESSAGE
    assert "verification tool" in BASE_DECIDE_HUMAN_MESSAGE  # named only as a forbidden term


def test_escalation_reason_asks_for_everyday_wording():
    assert "everyday words" in BASE_DECIDE_HUMAN_MESSAGE
    assert "which tool/API" not in BASE_DECIDE_HUMAN_MESSAGE
