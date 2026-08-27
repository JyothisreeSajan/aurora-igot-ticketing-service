"""
kafka_worker.py — Background worker that consumes resolution tickets from Kafka.

Reads from the Kafka topic configured via env vars and processes each ticket
through the full LangGraph resolution pipeline.

Run as a separate process:
    python kafka_worker.py               # single worker (default)
    python kafka_worker.py --workers 3   # 3 concurrent asyncio tasks

NOTE: All workers in the same process share one Kafka consumer in the same
consumer group. For true parallel consumption across multiple partitions,
run multiple processes (each gets its own consumer group member).
"""
import argparse
import asyncio
import logging
import time
from dotenv import load_dotenv

load_dotenv()

from app.core.graph.main_graph import arun_ticket
from app.core.graph.ticket_store import ticket_store
from app.core.utils.kafka_queue import consume_tickets
from app.core.utils.ticket_tracker import STAGE_IN_PROGRESS, ticket_tracker
from app.core.utils.token_tracker import token_tracker



logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)


# ── Core processing logic (same as queue_worker.py) ────────────────────────────

async def process_ticket(ticket_data: dict) -> bool:
    """Process a single ticket_data dict pulled from Kafka."""
    try:
        ticket_dict     = ticket_data["ticket_dict"]
        is_continuation = ticket_data["is_continuation"]
        existing_id     = ticket_data["existing_id"]
        tid             = ticket_dict.get("ticket_id")
        email           = ticket_dict.get("email", "")

        logger.info(f"[kafka-worker] Processing ticket={tid}")

        ticket_tracker.update_stage(
            ticket_id=tid,
            stage=STAGE_IN_PROGRESS,
            detail="Ticket dequeued from Kafka and graph execution started.",
            extra={"worker": "kafka_worker", "is_continuation": is_continuation},
        )

        started_at = time.monotonic()          # start timer for token flush
        result = await arun_ticket(ticket_dict)

        if is_continuation and existing_id:
            ticket_store.update_ticket(existing_id, result)
        else:
            ticket_store.create_ticket(result)

        ticket_tracker.complete(ticket_id=tid, result=result)
        token_tracker.flush(ticket_id=tid, result=result, started_at=started_at)

        logger.info(
            f"[kafka-worker] Completed ticket={tid} resolved={result.get('is_resolved')}"
        )
        return True

    except Exception as e:
        logger.error(f"[kafka-worker] Error processing ticket: {e}", exc_info=True)
        return False


# ── Worker loop ────────────────────────────────────────────────────────────────

async def worker_loop(worker_id: int, queue: asyncio.Queue):
    """Pull pre-fetched ticket dicts from the local asyncio queue and process them."""
    logger.info(f"[kafka-worker-{worker_id}] Started")
    while True:
        try:
            ticket_data = await queue.get()
            await process_ticket(ticket_data)
            queue.task_done()
        except Exception as e:
            logger.error(f"[kafka-worker-{worker_id}] Unexpected error: {e}")
            await asyncio.sleep(1)


async def kafka_consumer_loop(queue: asyncio.Queue):
    """
    Single Kafka consumer task that pushes deserialized ticket dicts
    into the shared asyncio queue for the worker pool.
    """
    logger.info("[kafka-consumer] Starting Kafka consumer loop...")
    async for ticket_data in consume_tickets(timeout_ms=3_000):
        if ticket_data is None:
            # No messages in this poll window — just keep going
            await asyncio.sleep(0.05)
            continue
        await queue.put(ticket_data)


async def run(num_workers: int = 1):
    """
    Launch one Kafka consumer task + *num_workers* asyncio worker tasks.
    The consumer feeds a shared in-process queue; workers drain it concurrently.
    """
    logger.info(f"[kafka-worker] Starting with {num_workers} worker(s)...")
    ticket_queue: asyncio.Queue = asyncio.Queue(maxsize=num_workers * 4)

    consumer_task = asyncio.create_task(kafka_consumer_loop(ticket_queue))
    worker_tasks  = [
        asyncio.create_task(worker_loop(i, ticket_queue))
        for i in range(num_workers)
    ]

    await asyncio.gather(consumer_task, *worker_tasks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kafka-based resolution ticket worker")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of concurrent processing workers (default: 1)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.workers))
