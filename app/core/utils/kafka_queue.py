"""
app/core/utils/kafka_queue.py
------------------------------
Kafka producer/consumer helpers for the iGOT Aurora resolution ticket pipeline.

Kafka producer/consumer implementation used by kafka_worker.py.

Producer (used by the /ingest endpoint or standalone):
    await produce_ticket(ticket_data)

Consumer (used by kafka_worker.py):
    async for ticket_data in consume_tickets():
        ...

Topic & broker settings are driven by env vars (see app/core/utils/config.py):
    KAFKA_BOOTSTRAP_SERVERS  (default: localhost:9092)
    KAFKA_TOPIC              (default: resolution_tickets)
    KAFKA_GROUP_ID           (default: aurora_resolution_workers)
"""

import json
import logging
from collections.abc import AsyncIterator

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaError

from app.core.utils.config import (
    KAFKA_BOOTSTRAP_SERVERS,
    KAFKA_GROUP_ID,
    KAFKA_TOPIC,
)

logger = logging.getLogger(__name__)


# ── Producer ───────────────────────────────────────────────────────────────────

async def produce_ticket(ticket_data: dict) -> bool:
    """
    Serialize *ticket_data* as JSON and publish it to the Kafka topic.

    A fresh producer is created per call (stateless, low-throughput ingest path).
    For high-throughput use-cases, initialise a long-lived producer at startup.

    Returns True on success, False on failure.
    """
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    try:
        await producer.start()
        tid = (ticket_data.get("ticket_dict") or ticket_data).get("ticket_id", "unknown")
        await producer.send_and_wait(KAFKA_TOPIC, ticket_data)
        logger.info(f"[kafka] Produced ticket_id={tid} → topic={KAFKA_TOPIC}")
        return True
    except KafkaError as e:
        logger.error(f"[kafka] Failed to produce ticket: {e}")
        return False
    except Exception as e:
        logger.error(f"[kafka] Unexpected error producing ticket: {e}")
        return False
    finally:
        await producer.stop()


# ── Consumer (async generator) ────────────────────────────────────────────────

async def consume_tickets(
    timeout_ms: int = 5_000,
) -> AsyncIterator[dict | None]:
    """
    Async generator that continuously polls *KAFKA_TOPIC* and yields
    deserialized ticket dicts.

    Yields None when a poll cycle returns nothing (so the caller can do
    housekeeping or apply a short sleep before the next iteration).

    Usage::

        async for ticket_data in consume_tickets():
            if ticket_data is None:
                await asyncio.sleep(0.1)
                continue
            # process ticket_data
    """
    consumer = AIOKafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=KAFKA_GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda raw: json.loads(raw.decode("utf-8")),
    )
    await consumer.start()
    logger.info(f"[kafka] Consumer started — topic={KAFKA_TOPIC} group={KAFKA_GROUP_ID}")
    try:
        while True:
            result = await consumer.getmany(timeout_ms=timeout_ms, max_records=1)
            if not result:
                yield None
                continue
            for _tp, messages in result.items():
                for msg in messages:
                    logger.info(
                        f"[kafka] Consumed offset={msg.offset} partition={msg.partition}"
                    )
                    yield msg.value
    finally:
        await consumer.stop()
        logger.info("[kafka] Consumer stopped.")
