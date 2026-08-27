"""
tools/zoho_tools.py
--------------------
LangChain tools and plain-Python helpers for Zoho Desk integration.

LangChain tools (bound to the LLM via bind_tools):
  - fetch_ticket_details    : fetch full Zoho Desk ticket details by ticket_id
  - update_zoho_ticket      : update a Zoho ticket status and resolution summary

Plain-Python helpers (called directly by graph nodes):
  - update_zoho_ticket_direct : programmatic Zoho ticket update without LLM binding
                                 (used by intake_node on junk/unregistered early exits)
"""
import json
import logging

from langchain_core.tools import tool as lc_tool

logger = logging.getLogger(__name__)

def update_zoho_ticket_direct(ticket_id: str, resolution_summary: str, status: str = "Resolved") -> str:
    """Update a Zoho Desk ticket with the given resolution summary and status.

    Plain Python function called directly by graph nodes (e.g. intake_node).
    Use update_zoho_ticket (LangChain @tool) when binding to the LLM.
    """
    logger.info(f"Updating Zoho ticket {ticket_id} with status '{status}': {resolution_summary}")
    return json.dumps({
        "ticket_id": ticket_id,
        "status": status,
        "resolution": resolution_summary,
        "message": "Ticket updated successfully."
    })

