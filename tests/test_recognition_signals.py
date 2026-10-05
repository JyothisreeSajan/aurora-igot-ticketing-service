import pytest

from app.core.utils.recognition_signals import resolve_sop, detect_portals
from app.core.utils.prompt_templates import (
    RECOGNITION_ENGAGEMENT_SOP_PROMPTS,
    RECOGNITION_ENGAGEMENT_SYSTEM_PROMPT,
)

R = "recognition_and_engagement"


@pytest.mark.parametrize("text,expected", [
    ("My Learning Hours is not reflected on eHRMS portal.", ["ehrms"]),
    ("learning hours not showing in e-HRMS", ["ehrms"]),
    ("APAR courses are not reflected on sparrow portal", ["sparrow"]),
    ("Shikshapath hours missing", ["shiksha"]),
    ("cbdt-karmayogi-shikshapath issue", ["shiksha"]),
    ("I work in CBDT and my organisation is wrong", []),   # org name, not the portal
    ("learning hours not reflecting", []),
    ("APAR training plan not visible", []),          # 'apar' alone is not a SPARROW signal
    ("hours missing in eHRMS and SPARROW", ["ehrms", "sparrow"]),
])
def test_detect_portals(text, expected):
    assert detect_portals(text) == expected


@pytest.mark.parametrize("sub,text,expected", [
    ("learning_hours_issue_ehrms", "learning hours not on eHRMS", "RE3"),
    ("learning_hours_issue_sparrow_apar", "APAR not in sparrow", "RE5"),
    ("learning_hours_issue_shiksha_path", "shiksha path hours", "RE4"),
    ("leaderboard_issue", "APAR courses not reflected on sparrow portal", "RE5"),   # case 2
    ("leaderboard_issue", "528 karma points but leaderboard rank shows 40", "RE6"),
    ("karma_points_issue", "course karma points not credited", "RE1"),
    ("weekly_claps_issue", "weekly clap reset", "RE2"),
    ("learning_hours_issue_ehrms", "my learning hours are not reflecting", "RE3"),   # no portal -> classifier's SOP
    ("learning_hours_issue_ehrms", "hours missing in eHRMS and SPARROW", "RE3"),     # several -> classifier's SOP
    ("learning_hours_issue_ehrms", "APAR courses not reflected on sparrow", "RE5"),  # text portal wins
    ("", "something unclear", "ALL"),
    ("", "hours not on eHRMS", "RE3"),
])
def test_resolve_sop(sub, text, expected):
    assert resolve_sop(sub, text) == expected


def test_every_resolved_sop_has_a_prompt_and_formats():
    for code, tmpl in RECOGNITION_ENGAGEMENT_SOP_PROMPTS.items():
        tmpl.format(email="u@x", main_category=R)
    RECOGNITION_ENGAGEMENT_SYSTEM_PROMPT.format(email="u@x", main_category=R)


def test_single_sop_prompt_excludes_other_sops():
    p = RECOGNITION_ENGAGEMENT_SOP_PROMPTS["RE5"]
    assert "SOP-RE5 Learning Hours Issue - SPARROW" in p
    assert "SOP-RE6 Leader Board Issue\n=" not in p
    assert "Weekly Claps Issue\n=" not in p


# ── subgraph prompt selection ────────────────────────────────────────────────

def _prompt(state):
    from app.core.graph.subgraphs.recognition_engagement_subgraph import RecognitionEngagementSubgraph
    return RecognitionEngagementSubgraph().system_prompt({"email": "u@x", "main_category": R, **state})


def test_subgraph_loads_only_the_matching_sop():
    p = _prompt({"sub_category": "learning_hours_issue_sparrow_apar",
                 "message": "APAR courses not reflected on sparrow"})
    assert "SOP-RE5 Learning Hours Issue - SPARROW" in p
    assert "SOP-RE6 Leader Board Issue\n=" not in p
    assert "ACTIVE SOP" in p and "SOP-RE5" in p.split("ACTIVE SOP")[1]


def test_subgraph_no_portal_uses_classifier_sop():
    p = _prompt({"sub_category": "learning_hours_issue_ehrms", "message": "my learning hours are missing"})
    assert "SOP-RE3 Learning Hours Issue - eHRMS" in p and "Portal Not Identified" not in p


def test_subgraph_continuation_uses_whole_thread():
    # first mail named no portal; the user's reply names it
    p = _prompt({"sub_category": "learning_hours_issue_ehrms", "message": "eHRMS",
                 "is_continuation": True,
                 "conversation_messages": [{"role": "user", "content": "learning hours missing"},
                                           {"role": "assistant", "content": "which portal?"},
                                           {"role": "user", "content": "eHRMS"}]})
    assert "SOP-RE3 Learning Hours Issue - eHRMS" in p


def test_subgraph_unknown_sub_falls_back_to_full_prompt():
    p = _prompt({"sub_category": "", "message": "something unclear"})
    assert "SOP-RE1" in p and "SOP-RE6 Leader Board Issue\n=" in p and "ACTIVE SOP" not in p


# ── case-2 safety net lives in the subgraph ──────────────────────────────────


def test_subgraph_fixes_wrong_sub_category():
    # the case-2 safety net lives in the subgraph, independent of the intake flag
    assert resolve_sop("leaderboard_issue", "APAR courses are not reflected on sparrow portal") == "RE5"
    assert resolve_sop("leaderboard_issue", "what is sparrow") == "RE6"   # no problem word -> keep sub


# ── remaining resolve_sop / _ticket_text branches ────────────────────────────

def test_resolve_sop_unknown_sub_category_falls_back_to_all():
    assert resolve_sop("some_future_sub_category", "hours missing on sparrow") == "ALL"


def test_resolve_sop_empty_sub_without_portal_falls_back_to_all():
    assert resolve_sop("", "") == "ALL"
    assert resolve_sop(None, "no portal here") == "ALL"


def test_detect_portals_handles_empty_and_none():
    assert detect_portals("") == []
    assert detect_portals(None) == []


def test_ticket_text_continuation_without_user_messages_uses_message():
    from app.core.graph.subgraphs.recognition_engagement_subgraph import RecognitionEngagementSubgraph
    sg = RecognitionEngagementSubgraph()
    state = {"is_continuation": True, "message": "latest reply",
             "conversation_messages": [{"role": "assistant", "content": "question?"}]}
    assert sg._ticket_text(state) == "latest reply"
    assert sg._ticket_text({"is_continuation": True, "message": "m"}) == "m"
    assert sg._ticket_text({}) == ""


# ── get_tools / greeting-name helpers of the R&E subgraph ────────────────────

def _tool_result(tool, payload):
    import json
    return {"tool": tool, "summary": json.dumps(payload)}


class TestRecognitionEngagementSubgraphHelpers:
    def test_get_tools_returns_recognition_engagement_tools(self):
        from app.core.graph.subgraphs.recognition_engagement_subgraph import RecognitionEngagementSubgraph
        from app.core.tools.recognition_engagement_tools import get_recognition_engagement_tools

        names = [t.name for t in RecognitionEngagementSubgraph().get_tools({})]
        assert names == [t.name for t in get_recognition_engagement_tools()]
        assert names  # not empty

    @pytest.mark.parametrize("results,expected", [
        ([_tool_result("get_user_first_name", {"firstName": "Meera"})], "Meera"),
        ([_tool_result("get_user_profile", {"first_name": "Bharath"})], "Bharath"),
        ([_tool_result("get_user_first_name", {"firstName": "First"}),
          _tool_result("get_user_profile", {"firstName": "Second"})], "Second"),
        ([_tool_result("get_mdo_details", {"firstName": "Ignored"})], None),
        ([{"tool": "get_user_profile", "summary": "not json"}], None),
        ([_tool_result("get_user_profile", {"other": "x"})], None),
        ([], None),
    ])
    def test_extract_first_name(self, results, expected):
        from app.core.graph.subgraphs.recognition_engagement_subgraph import RecognitionEngagementSubgraph
        assert RecognitionEngagementSubgraph()._extract_first_name(results) == expected

    def test_execute_node_merges_first_name(self):
        from unittest.mock import patch
        from app.core.graph.subgraphs.recognition_engagement_subgraph import RecognitionEngagementSubgraph
        with patch("app.core.graph.subgraphs.base_subgraph.BaseSubgraph.execute_node") as sup:
            sup.return_value = {"tool_results": [_tool_result("get_user_first_name", {"firstName": "Meera"})]}
            assert RecognitionEngagementSubgraph().execute_node({"ticket_id": "t"})["user_first_name"] == "Meera"

    def test_execute_node_adds_nothing_without_name(self):
        from unittest.mock import patch
        from app.core.graph.subgraphs.recognition_engagement_subgraph import RecognitionEngagementSubgraph
        with patch("app.core.graph.subgraphs.base_subgraph.BaseSubgraph.execute_node") as sup:
            sup.return_value = {"tool_results": []}
            assert "user_first_name" not in RecognitionEngagementSubgraph().execute_node({"ticket_id": "t"})
