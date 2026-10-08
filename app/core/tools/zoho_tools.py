"""
tools/zoho_tools.py
--------------------
Plain-Python helpers for Zoho Desk integration.

Functions called directly by graph nodes:
  - update_zoho_ticket_direct : creates a DRAFT reply on the Zoho ticket so
                                the L1/HIL agent can review and send it.
                                Nothing is sent to the customer automatically.
                                No ticket status is changed.
  - add_zoho_ticket_comment   : adds a PRIVATE comment explaining why no draft
                                was created (e.g. category out of scope).
"""
import asyncio
import logging

from app.core.utils.constants import ENABLE_ZOHO_TICKET_UPDATE

logger = logging.getLogger(__name__)


def update_zoho_ticket_direct(
    ticket_id: str,
    resolution_summary: str,
    status: str = "Resolved",   # kept for call-site compatibility; not used
) -> str:
    """
    Creates a DRAFT reply on the Zoho Desk ticket with the AI-generated
    resolution so the L1 support agent can review and send it manually.

    - No email is sent to the customer.
    - No ticket status is changed.
    - The `status` param is accepted for backward compatibility but ignored;
      only the HIL agent can close/resolve the ticket after review.

    Called directly by graph nodes (intake_node, ticket_tools).
    """
    if not ENABLE_ZOHO_TICKET_UPDATE:
        logger.info(
            f"[zoho_tools] Zoho ticket update disabled by ENABLE_ZOHO_TICKET_UPDATE feature flag. "
            f"Skipping draft reply for ticket {ticket_id}."
        )
        return ""

    from app.services.zoho_service import (
        ZohoAPIError,
        create_draft_reply,
        get_ticket_details,
        ensure_aurora_tag,
    )

    logger.info(
        f"[zoho_tools] Creating draft reply for ticket {ticket_id} "
        f"(requested status='{status}' ignored — HIL workflow)"
    )

    async def _run() -> dict:
        # Fetch the customer's email from the ticket to use as the 'to' address
        try:
            ticket = await get_ticket_details(ticket_id)
            to_address = ticket.get("email") or ""
        except Exception as e:
            logger.warning(
                f"[zoho_tools] Could not fetch ticket details for {ticket_id}: {e}. "
                "Draft will be created without 'to' address."
            )
            to_address = ""

        # Create the draft first — only tag the ticket once we know the draft
        # was successfully created, so we never leave a tag with no draft behind it.
        result = await create_draft_reply(
            ticket_id=ticket_id,
            content=resolution_summary,
            to=to_address,
        )

        draft_id = result.get("id") if isinstance(result, dict) else None
        if draft_id:
            # Draft confirmed — now safe to tag the ticket
            try:
                tag_result = await ensure_aurora_tag(ticket_id)
                logger.info(
                    f"[zoho_tools] Tag association result for ticket {ticket_id}: "
                    f"{tag_result}"
                )
            except Exception as e:
                logger.warning(
                    f"[zoho_tools] Failed to ensure 'aurora' tag for ticket {ticket_id}: {e}"
                )
        else:
            logger.warning(
                f"[zoho_tools] Draft creation returned no id for ticket {ticket_id} "
                f"(response={result}). Skipping tag update."
            )

        return result

    try:
        # Graph nodes are sync; run the async draft call in a new event loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already inside an async context (e.g. called from an async node)
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _run())
                result = future.result(timeout=30)
        else:
            result = asyncio.run(_run())

        draft_id = result.get("id", "unknown")
        logger.info(
            f"[zoho_tools] Draft reply created: draft_id={draft_id} "
            f"ticket={ticket_id} status={result.get('status')}"
        )
        return draft_id

    except ZohoAPIError as e:
        logger.error(f"[zoho_tools] Zoho API error for ticket {ticket_id}: {e}")
        return ""
    except Exception as e:
        logger.error(f"[zoho_tools] Unexpected error for ticket {ticket_id}: {e}")
        return ""


OUT_OF_SCOPE_COMMENT = (
    "The user's message is out of scope for the Aurora agent (category not "
    "enabled), so no draft reply was created. Please review the ticket and "
    "assist the user."
)


def add_zoho_ticket_comment(ticket_id: str, content: str) -> bool:
    """
    Adds a private comment to the Zoho ticket (retried, duplicate-safe).
    Never raises — a failed comment must not break the ticket flow.
    Returns True if the comment was added or already present.
    """
    if not ENABLE_ZOHO_TICKET_UPDATE:
        logger.info(
            f"[zoho_tools] Zoho ticket update disabled by ENABLE_ZOHO_TICKET_UPDATE "
            f"feature flag. Skipping comment for ticket {ticket_id}."
        )
        return False

    from app.services.zoho_service import add_private_comment

    try:
        try:
            asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            in_loop = False

        if in_loop:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pool.submit(asyncio.run, add_private_comment(ticket_id, content)).result(timeout=60)
        else:
            asyncio.run(add_private_comment(ticket_id, content))
        return True
    except Exception as e:
        logger.error(f"[zoho_tools] Failed to add comment to ticket {ticket_id}: {e}")
        return False
