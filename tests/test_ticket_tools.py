"""
Unit tests for app/core/tools/ticket_tools.py:
  - notify_user: placeholder unmasking applied just before the Zoho draft is
    created, and the Zoho draft-vs-private-comment decision.
  - send_to_human_queue: private Zoho comment on every escalation.
"""

from unittest.mock import patch

import pytest

from app.core.tools.ticket_tools import notify_user, send_to_human_queue
from app.core.tools.zoho_tools import OUT_OF_SCOPE_COMMENT


@pytest.fixture
def comment():
    with patch("app.core.tools.ticket_tools.add_zoho_ticket_comment") as m:
        yield m


@pytest.fixture
def zoho(comment):
    with patch("app.core.tools.ticket_tools.update_zoho_ticket_direct") as m, \
         patch("app.core.tools.ticket_tools.log_ticket_outcome"):
        yield m


def _state(**over):
    base = {
        "ticket_id": "T1",
        "email": "owner@x.gov.in",
        "final_response": "Current Email ID: {{USER_EMAIL}}",
        "is_resolved": True,
    }
    base.update(over)
    return base


class TestNotifyUserUnmasking:
    def test_user_email_token_resolved_from_state_email(self, zoho):
        out = notify_user(_state())

        assert out["final_response"] == "Current Email ID: owner@x.gov.in"
        zoho.assert_called_once_with("T1", "Current Email ID: owner@x.gov.in", "Resolved")

    def test_tool_supplied_tokens_are_resolved_alongside_user_email(self, zoho):
        out = notify_user(_state(
            final_response="{{USER_EMAIL}} -> {{NEW_CONTACT}}",
            spoc_replacements={"{{NEW_CONTACT}}": "9876543210"},
        ))

        assert out["final_response"] == "owner@x.gov.in -> 9876543210"

    def test_tool_supplied_user_email_is_not_overridden_by_state_email(self, zoho):
        out = notify_user(_state(spoc_replacements={"{{USER_EMAIL}}": "tool@x.gov.in"}))

        assert out["final_response"] == "Current Email ID: tool@x.gov.in"

    def test_no_state_email_leaves_user_email_token_untouched(self, zoho):
        out = notify_user(_state(email=""))

        assert out["final_response"] == "Current Email ID: {{USER_EMAIL}}"

    def test_empty_response_is_left_alone(self, zoho):
        out = notify_user(_state(final_response=""))

        assert out["final_response"] == ""

    def test_state_spoc_replacements_not_mutated(self, zoho):
        original = {"{{NEW_CONTACT}}": "x@y.com"}
        notify_user(_state(spoc_replacements=original))

        assert original == {"{{NEW_CONTACT}}": "x@y.com"}


class TestNotifyUserZohoDraftOrComment:
    def test_resolved_ticket_creates_draft_and_no_comment(self, zoho, comment):
        notify_user(_state())

        zoho.assert_called_once()
        comment.assert_not_called()

    def test_escalated_ticket_skips_draft_and_adds_comment(self, zoho, comment):
        notify_user(_state(escalated_to_human=True))

        zoho.assert_not_called()
        comment.assert_called_once_with("T1", OUT_OF_SCOPE_COMMENT)

    def test_comment_includes_the_reason_when_there_is_one(self, zoho, comment):
        notify_user(_state(escalated_to_human=True, handoff_reason="MDO details could not be fetched."))

        comment.assert_called_once_with("T1", f"{OUT_OF_SCOPE_COMMENT}<br>MDO details could not be fetched.")

    def test_unresolved_ticket_skips_draft_and_adds_comment(self, zoho, comment):
        notify_user(_state(is_resolved=False))

        zoho.assert_not_called()
        comment.assert_called_once_with("T1", OUT_OF_SCOPE_COMMENT)

    @pytest.mark.parametrize("flag", ["is_junk", "is_category_disabled"])
    def test_junk_and_disabled_category_get_no_draft_and_no_comment(self, zoho, comment, flag):
        notify_user(_state(**{flag: True}))

        zoho.assert_not_called()
        comment.assert_not_called()

    def test_outcome_still_logged_after_comment(self, zoho):
        with patch("app.core.tools.ticket_tools.log_ticket_outcome") as log:
            notify_user(_state(escalated_to_human=True))

        log.assert_called_once()
        assert log.call_args.kwargs["outcome"] == "skipped_zoho"


class TestSendToHumanQueue:
    def test_adds_private_comment_and_never_creates_draft(self, zoho, comment):
        send_to_human_queue({"ticket_id": "T1", "escalation_reason": "low confidence"})

        comment.assert_called_once_with("T1", OUT_OF_SCOPE_COMMENT)
        zoho.assert_not_called()

    def test_human_queue_comment_includes_the_reason_when_there_is_one(self, zoho, comment):
        send_to_human_queue({"ticket_id": "T1", "handoff_reason": "No MDO or YP contact was found."})

        comment.assert_called_once_with("T1", f"{OUT_OF_SCOPE_COMMENT}<br>No MDO or YP contact was found.")

    def test_non_text_reason_gives_the_standard_comment(self, zoho, comment):
        send_to_human_queue({"ticket_id": "T1", "handoff_reason": 5})

        comment.assert_called_once_with("T1", OUT_OF_SCOPE_COMMENT)

    def test_comment_added_even_without_escalation_reason(self, zoho, comment):
        send_to_human_queue({"ticket_id": "T1"})

        comment.assert_called_once_with("T1", OUT_OF_SCOPE_COMMENT)

    def test_marks_escalated_and_logs_outcome(self, zoho):
        with patch("app.core.tools.ticket_tools.log_ticket_outcome") as log:
            out = send_to_human_queue({"ticket_id": "T1"})

        assert out["escalated_to_human"] is True
        log.assert_called_once()
        assert log.call_args.kwargs["outcome"] == "escalated_to_human"
