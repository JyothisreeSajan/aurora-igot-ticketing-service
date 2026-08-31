"""
tests/test_pii_masking.py
--------------------------
Tests for PII masking engine (_PIIMasker / mask_pii / mask_pii_default)
and end-to-end ticket ingestion through the graph workflow.
"""

from unittest.mock import MagicMock, patch
from langchain_core.messages import AIMessage

from app.core.utils.helpers import mask_pii, mask_pii_default
from app.core.graph.nodes.intake_node import intake_node
from app.core.graph.subgraphs.certificate_subgraph import CertificateSubgraph


# ── 1. Unit Tests for PII Detection & Masking ──────────────────────────────────

def test_mask_pii_emails():
    """Verify that government (.gov.in, .nic.in) and standard email addresses are masked."""
    text = "Please write to john.doe@gov.in or support@nic.in or user@example.com for help."
    masked = mask_pii_default(text)
    assert "john.doe@gov.in" not in masked
    assert "support@nic.in" not in masked
    assert "user@example.com" not in masked
    assert "<EMAIL_ADDRESS>" in masked


def test_mask_pii_phone_numbers():
    """Verify that Indian phone numbers with various formats are masked."""
    text = "Call me at +91-9876543210 or 9876543210 or +91 9876543210."
    masked = mask_pii_default(text)
    assert "+91-9876543210" not in masked
    assert "9876543210" not in masked
    assert "<PHONE_NUMBER>" in masked


def test_mask_pii_aadhaar():
    """Verify that Indian Aadhaar numbers (12 digits) are masked."""
    text = "My Aadhaar number is 3678 1234 5678, please update my profile."
    masked = mask_pii_default(text)
    assert "3678 1234 5678" not in masked
    assert "<IN_AADHAAR>" in masked


def test_mask_pii_pan():
    """Verify that Indian PAN numbers (10 char alphanumeric) are masked."""
    text = "My PAN card is ABCDE1234F for verification."
    masked = mask_pii_default(text)
    assert "ABCDE1234F" not in masked
    assert "<IN_PAN>" in masked


def test_mask_pii_combined():
    """Verify combined PII text masking."""
    raw_text = (
        "User Rakesh Kumar (email: rakesh.k@gov.in, mobile: +91-9876543210, "
        "Aadhaar: 2345 6789 0123, PAN: XYZAB5678C) requested certificate assistance."
    )
    masked = mask_pii_default(raw_text)
    assert "rakesh.k@gov.in" not in masked
    assert "+91-9876543210" not in masked
    assert "2345 6789 0123" not in masked
    assert "XYZAB5678C" not in masked
    assert "<EMAIL_ADDRESS>" in masked
    assert "<PHONE_NUMBER>" in masked
    assert "<IN_AADHAAR>" in masked
    assert "<IN_PAN>" in masked


# ── 2. Workflow Ingestion & Graph Nodes PII Masking Tests ─────────────────────

@patch("app.core.graph.nodes.intake_node.fetch_user_info", return_value=(True, "User"))
@patch("app.core.graph.nodes.intake_node._llm")
def test_intake_node_pii_masking(mock_llm, mock_fetch_user):
    """Test that intake_node masks PII in user message before calling junk/classification LLM."""
    mock_response = MagicMock()
    mock_response.content = '{"category": "certificate", "main_category": "certificate", "sub_category": "Issue in Certificate Download", "confidence": 0.9, "reason": "Certificate issue"}'
    mock_response.usage_metadata = {}
    mock_llm.invoke.return_value = mock_response

    state = {
        "ticket_id": "t_pii_test_001",
        "email": "user@gov.in",
        "message": "Certificate missing for rkumar@gov.in, phone +91-9876543210, Aadhaar 3678 1234 5678",
        "channel": "email",
        "graph_plan": [],
        "retry_count": 0,
    }

    result = intake_node(state)

    # Verify LLM was invoked with masked message in prompt
    assert mock_llm.invoke.called
    called_messages = mock_llm.invoke.call_args[0][0]
    human_msg = called_messages[1].content

    # Assert raw PII was masked out of the prompt sent to LLM
    assert "rkumar@gov.in" not in human_msg
    assert "+91-9876543210" not in human_msg
    assert "3678 1234 5678" not in human_msg
    assert "<EMAIL_ADDRESS>" in human_msg
    assert "<PHONE_NUMBER>" in human_msg
    assert "<IN_AADHAAR>" in human_msg


@patch("app.core.graph.subgraphs.base_subgraph._llm")
def test_subgraph_plan_node_pii_masking(mock_llm):
    """Test that subgraph plan_node masks PII in user message before calling LLM."""
    subgraph = CertificateSubgraph()

    mock_llm_response = MagicMock(spec=AIMessage)
    mock_llm_response.content = "Plan: 1. Verify user profile, 2. Issue certificate."
    mock_llm_response.usage_metadata = {}
    mock_llm.invoke.return_value = mock_llm_response

    state = {
        "ticket_id": "t_pii_test_002",
        "email": "official@gov.in",
        "message": "My email is official.user@gov.in, PAN is ABCDE1234F, call +91-9876543210",
        "main_category": "certificate",
        "retry_count": 0,
        "enriched_context": {"kb_snippets": []},
    }

    new_state = subgraph.plan_node(state)

    # Verify plan was created
    assert "plan" in new_state
    assert new_state["plan"] == "Plan: 1. Verify user profile, 2. Issue certificate."

    # Verify LLM prompt received masked text
    mock_llm.invoke.assert_called_once()
    called_messages = mock_llm.invoke.call_args[0][0]
    human_msg = called_messages[1].content

    assert "official.user@gov.in" not in human_msg
    assert "ABCDE1234F" not in human_msg
    assert "+91-9876543210" not in human_msg
    assert "<EMAIL_ADDRESS>" in human_msg
    assert "<IN_PAN>" in human_msg
    assert "<PHONE_NUMBER>" in human_msg
