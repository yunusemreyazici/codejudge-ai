from uuid import uuid4

import pytest

from tests.queue.conftest import RedisHarness

pytestmark = pytest.mark.queue


async def test_enqueue_contains_only_evaluation_identity(redis_harness: RedisHarness) -> None:
    evaluation_id = uuid4()

    message_id = await redis_harness.queue.enqueue(evaluation_id)
    entries = await redis_harness.raw.xrange(redis_harness.queue.stream)

    assert entries == [(message_id, {"evaluation_id": str(evaluation_id)})]


async def test_consume_acknowledge_and_pending_state(redis_harness: RedisHarness) -> None:
    evaluation_id = uuid4()
    await redis_harness.queue.enqueue(evaluation_id)

    message = await redis_harness.queue.consume("worker-a", block_ms=10)
    assert message is not None
    assert message.evaluation_id == evaluation_id
    pending = await redis_harness.raw.xpending(
        redis_harness.queue.stream, redis_harness.queue.group
    )
    assert pending["pending"] == 1

    await redis_harness.queue.acknowledge(message.message_id)
    pending = await redis_harness.raw.xpending(
        redis_harness.queue.stream, redis_harness.queue.group
    )
    assert pending["pending"] == 0


async def test_unacknowledged_message_can_be_reclaimed(redis_harness: RedisHarness) -> None:
    evaluation_id = uuid4()
    await redis_harness.queue.enqueue(evaluation_id)
    original = await redis_harness.queue.consume("dead-worker", block_ms=10)
    assert original is not None

    reclaimed = await redis_harness.queue.reclaim("replacement-worker", minimum_idle_ms=0)

    assert reclaimed is not None
    assert reclaimed.message_id == original.message_id
    assert reclaimed.evaluation_id == evaluation_id
    await redis_harness.queue.acknowledge(reclaimed.message_id)


async def test_group_creation_and_duplicate_publication_are_safe(
    redis_harness: RedisHarness,
) -> None:
    evaluation_id = uuid4()
    await redis_harness.queue.ensure_group()

    first = await redis_harness.queue.enqueue(evaluation_id)
    second = await redis_harness.queue.enqueue(evaluation_id)

    assert first != second
    first_message = await redis_harness.queue.consume("worker", block_ms=10)
    second_message = await redis_harness.queue.consume("worker", block_ms=10)
    assert first_message is not None and second_message is not None
    assert first_message.evaluation_id == second_message.evaluation_id == evaluation_id


async def test_connection_reconnect_and_worker_heartbeat(redis_harness: RedisHarness) -> None:
    await redis_harness.raw.connection_pool.disconnect()

    assert await redis_harness.queue.check_capability() is True
    await redis_harness.queue.heartbeat("worker-test", ttl_seconds=5)
    assert await redis_harness.queue.active_workers() == 1
