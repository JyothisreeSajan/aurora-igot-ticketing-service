"""
Unit tests for _recover_new_contact / _classify_contact_context in
app/core/graph/subgraphs/base_subgraph.py.

Covers the deterministic "which email/mobile is the NEW one" detection used
by the multi-account (SOP-A2) flow, including tickets that mention both the
old and new contact in the same message.
"""

from app.core.graph.subgraphs.base_subgraph import (
    _classify_contact_context,
    _recover_new_contact,
)


class TestClassifyContactContext:
    def test_new_cue_word_before_match(self):
        message = "the updated mail id is y@new.com"
        start = message.index("y@new.com")
        assert _classify_contact_context(message, start) == "new"

    def test_old_cue_word_before_match(self):
        message = "still showing x@old.com in my profile"
        start = message.index("x@old.com")
        assert _classify_contact_context(message, start) == "old"

    def test_no_cue_words_is_unknown(self):
        message = "please check a@b.com for me"
        start = message.index("a@b.com")
        assert _classify_contact_context(message, start) == "unknown"

    def test_nearest_cue_wins_when_both_present(self):
        message = "old contact was x@old.com but now the new one is y@new.com"
        old_start = message.index("x@old.com")
        new_start = message.index("y@new.com")
        assert _classify_contact_context(message, old_start) == "old"
        assert _classify_contact_context(message, new_start) == "new"


class TestRecoverNewContact:
    def test_prefers_email_with_new_cue_over_owner_exclusion(self):
        message = "my profile still shows x@old.com, please update it to y@new.com"
        result = _recover_new_contact(message, owner_email="x@old.com")
        assert result == "y@new.com"

    def test_owners_email_can_be_the_new_one_when_tagged_new(self):
        # Owner's known email IS the new one; naive "exclude owner email" would
        # have picked the old contact instead.
        message = "it still shows old@x.com, kindly update to owner@new.com"
        result = _recover_new_contact(message, owner_email="owner@new.com")
        assert result == "owner@new.com"

    def test_falls_back_to_owner_exclusion_when_context_ambiguous(self):
        message = "please check owner@x.com and other@y.com"
        result = _recover_new_contact(message, owner_email="owner@x.com")
        assert result == "other@y.com"

    def test_single_email_no_owner_match(self):
        message = "my new email should be single@x.com"
        result = _recover_new_contact(message, owner_email="different@x.com")
        assert result == "single@x.com"

    def test_falls_back_to_mobile_when_no_email_present(self):
        message = "please update my number to 9876543210 (new one)"
        result = _recover_new_contact(message, owner_email="owner@x.com")
        assert result == "9876543210"

    def test_returns_none_when_no_contact_present(self):
        result = _recover_new_contact("no contact info here", owner_email="owner@x.com")
        assert result is None

    def test_empty_message(self):
        assert _recover_new_contact("", owner_email="owner@x.com") is None
        assert _recover_new_contact(None, owner_email="owner@x.com") is None
