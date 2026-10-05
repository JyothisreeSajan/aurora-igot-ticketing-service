"""
recognition_signals.py
----------------------
Deterministic (non-LLM) SOP selection for the recognition_and_engagement subgraph.

resolve_sop() decides which single SOP (RE1..RE6) the subgraph should load from
the intake sub_category plus the ticket text, so the model is never asked to pick
one SOP out of six. Classification itself is left entirely to the LLM intake step.
"""

import re

SUB_EHRMS    = "learning_hours_issue_ehrms"
SUB_SHIKSHA  = "learning_hours_issue_shiksha_path"
SUB_SPARROW  = "learning_hours_issue_sparrow_apar"

LEARNING_HOURS_SUBS = frozenset({SUB_EHRMS, SUB_SHIKSHA, SUB_SPARROW})

_SUB_TO_SOP = {
    "karma_points_issue":  "RE1",
    "weekly_claps_issue":  "RE2",
    SUB_EHRMS:             "RE3",
    SUB_SHIKSHA:           "RE4",
    SUB_SPARROW:           "RE5",
    "leaderboard_issue":   "RE6",
}
_PORTAL_TO_SOP = {"ehrms": "RE3", "shiksha": "RE4", "sparrow": "RE5"}

# Portal names. "apar" alone is deliberately NOT a SPARROW signal — it also
# appears in iGOT-internal training-plan tickets (ca_apar_issue).
_PORTAL_PATTERNS = {
    "ehrms":   re.compile(r"\be[\s\-.]?hrms\b|\bhrms\b", re.I),
    # No bare "cbdt": it is also an organisation name (Central Board of Direct Taxes).
    # The CBDT-branded portal name still matches via its "shikshapath" part.
    "shiksha": re.compile(r"\bs(?:h)?iksha(?:[\s\-_]?path)?\b", re.I),
    "sparrow": re.compile(r"\bsparrow\b", re.I),
}

# A problem is being reported (vs. an informational "what is SPARROW?" question).
_PROBLEM_WORDS = re.compile(
    r"\bnot\b|n['\u2019]t\b|\bno\b|missing|unable|cannot|can\s?not|fail|error|issue|"
    r"problem|wrong|incorrect|pending|delay|\byet\b|\bstill\b|\bnever\b|without",
    re.I,
)

# Words that mean the ticket is about another R&E topic, not a portal sync issue.
_OTHER_TOPIC = re.compile(
    r"leader\s?board|top\s+karmayogi|\brank(?:ing)?\b|karma\s*points?|\bclaps?\b",
    re.I,
)


def detect_portals(text: str) -> list[str]:
    """Return the distinct external portals named in the text (stable order)."""
    return [name for name, pat in _PORTAL_PATTERNS.items() if pat.search(text or "")]


def resolve_sop(sub_category: str, text: str) -> str:
    """
    Pick the single SOP the subgraph should load.

    Returns "RE1".."RE6", or "ALL" (no usable signal — caller falls back to the
    full multi-SOP prompt).

    A portal named in the text wins over the classifier's sub-category for
    Learning Hours tickets. When the text names no portal (or several), the
    classifier's sub-category decides.
    """
    portals = detect_portals(text)
    sub = sub_category or ""
    single_portal = _PORTAL_TO_SOP[portals[0]] if len(portals) == 1 else None

    if sub == "":
        return single_portal or "ALL"

    sop = _SUB_TO_SOP.get(sub)
    if sop is None:
        return "ALL"

    if sub in LEARNING_HOURS_SUBS:
        return single_portal or sop

    # Classifier picked karma/claps/leaderboard, but the text names a portal and
    # none of those topics -> it is really a portal sync question.
    if single_portal and not _OTHER_TOPIC.search(text) and _PROBLEM_WORDS.search(text):
        return single_portal
    return sop
