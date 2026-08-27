from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.queue.redis_streams import RedisStreamsQueue
from tests.database.conftest import database_harness as database_harness


@dataclass(frozen=True, slots=True)
class RedisHarness:
    queue: RedisStreamsQueue
    raw: Redis


@pytest_asyncio.fixture
async def redis_harness() -> AsyncIterator[RedisHarness]:
    redis_url = os.getenv("CODEJUDGE_TEST_REDIS_URL", "").strip()
    if not redis_url:
        pytest.skip("CODEJUDGE_TEST_REDIS_URL is not configured")
    database_path = urlparse(redis_url).path.lstrip("/")
    if not database_path.isdigit() or int(database_path) == 0:
        raise RuntimeError("Queue tests require a dedicated nonzero Redis database")
    raw: Redis = Redis.from_url(redis_url, decode_responses=True)
    await raw.flushdb()
    identity = uuid4().hex
    queue = RedisStreamsQueue(
        redis_url,
        stream=f"codejudge:test:{identity}",
        group=f"codejudge-test-{identity}",
    )
    await queue.ensure_group()
    try:
        yield RedisHarness(queue=queue, raw=raw)
    finally:
        await raw.flushdb()
        await queue.close()
        await raw.aclose()
