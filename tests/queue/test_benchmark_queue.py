import os
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.benchmarks.queue import BenchmarkOutboxPublisher, BenchmarkQueue
from tests.database.conftest import DatabaseHarness
from tests.database.test_benchmark_repository import _plan
from tests.queue.conftest import RedisHarness

pytestmark = [pytest.mark.queue, pytest.mark.database]


async def test_benchmark_stream_delivery_acknowledgement_and_outbox(
    database_harness: DatabaseHarness,
    redis_harness: RedisHarness,
) -> None:
    del redis_harness
    redis_url = os.environ["CODEJUDGE_TEST_REDIS_URL"]
    assert int(urlparse(redis_url).path.lstrip("/")) > 0
    identity = uuid4().hex
    queue = BenchmarkQueue(
        redis_url,
        stream=f"codejudge:benchmark-test:{identity}",
        group=f"codejudge-benchmark-test-{identity}",
    )
    await queue.ensure_group()
    run, config, sample = _plan(idempotency_key=None)
    await database_harness.benchmark_repository.create_plan(run, [config], [sample])
    publisher = BenchmarkOutboxPublisher(
        database_harness.benchmark_repository,
        queue,
        retry_base_delay_seconds=0.01,
    )

    assert await publisher.dispatch_once() == 1
    assert await publisher.dispatch_once() == 0
    message = await queue.consume("worker", block_ms=100)
    assert message is not None
    assert message.benchmark_sample_id == sample.benchmark_sample_id
    assert await queue.pending_count() == 1
    await queue.acknowledge(message.message_id)
    assert await queue.pending_count() == 0
    await queue.close()
