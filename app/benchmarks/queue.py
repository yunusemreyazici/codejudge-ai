"""Separate Redis Stream delivery for durable benchmark sample IDs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from app.benchmarks.repositories import BenchmarkRepository
from app.jobs.models import OutboxEvent
from app.jobs.service import utc_now
from app.queue.redis_streams import QueueUnavailableError

DEFAULT_BENCHMARK_STREAM = "codejudge:benchmark-samples"
DEFAULT_BENCHMARK_GROUP = "codejudge-benchmark-workers"


class BenchmarkQueueMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    message_id: str
    benchmark_sample_id: UUID


class BenchmarkQueueProtocol(Protocol):
    async def acknowledge(self, message_id: str) -> None: ...


class BenchmarkQueue:
    def __init__(
        self,
        redis_url: str,
        *,
        stream: str = DEFAULT_BENCHMARK_STREAM,
        group: str = DEFAULT_BENCHMARK_GROUP,
    ) -> None:
        self._redis: Redis = Redis.from_url(redis_url, decode_responses=True)
        self.stream = stream
        self.group = group

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise QueueUnavailableError(
                    "Benchmark queue consumer group is unavailable."
                ) from error
        except RedisError as error:
            raise QueueUnavailableError("Benchmark queue is unavailable.") from error

    async def enqueue(self, sample_id: UUID) -> str:
        try:
            result = await self._redis.xadd(self.stream, {"benchmark_sample_id": str(sample_id)})
        except RedisError as error:
            raise QueueUnavailableError("Benchmark queue is unavailable.") from error
        return str(result)

    async def consume(self, consumer: str, block_ms: int = 1000) -> BenchmarkQueueMessage | None:
        try:
            response: Any = await self._redis.xreadgroup(
                self.group,
                consumer,
                {self.stream: ">"},
                count=1,
                block=block_ms,
            )
        except RedisError as error:
            raise QueueUnavailableError("Benchmark queue is unavailable.") from error
        if not response:
            return None
        return _parse_message(response[0][1][0])

    async def reclaim(self, consumer: str, minimum_idle_ms: int) -> BenchmarkQueueMessage | None:
        try:
            response: Any = await self._redis.xautoclaim(
                self.stream,
                self.group,
                consumer,
                min_idle_time=minimum_idle_ms,
                start_id="0-0",
                count=1,
            )
        except RedisError as error:
            raise QueueUnavailableError("Benchmark queue is unavailable.") from error
        messages = response[1] if response and len(response) > 1 else []
        return None if not messages else _parse_message(messages[0])

    async def acknowledge(self, message_id: str) -> None:
        try:
            await self._redis.xack(self.stream, self.group, message_id)
        except RedisError as error:
            raise QueueUnavailableError("Benchmark queue acknowledgement failed.") from error

    async def pending_count(self) -> int:
        try:
            summary: Any = await self._redis.xpending(self.stream, self.group)
        except RedisError as error:
            raise QueueUnavailableError("Benchmark queue pending state is unavailable.") from error
        return int(summary.get("pending", 0)) if isinstance(summary, dict) else int(summary[0])

    async def close(self) -> None:
        await self._redis.aclose()


class BenchmarkOutboxPublisher:
    def __init__(
        self,
        repository: BenchmarkRepository,
        queue: BenchmarkQueue,
        *,
        retry_base_delay_seconds: float,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._retry_base_delay_seconds = retry_base_delay_seconds

    async def dispatch_once(self, limit: int = 100) -> int:
        events: Sequence[OutboxEvent] = await self._repository.ready_outbox(utc_now(), limit)
        published = 0
        for event in events:
            try:
                await self._queue.enqueue(event.aggregate_id)
            except QueueUnavailableError:
                await self._repository.mark_outbox_failed(
                    event.event_id, utc_now(), self._retry_base_delay_seconds
                )
                continue
            await self._repository.mark_outbox_published(event.event_id, utc_now())
            published += 1
        return published


def _parse_message(raw: Any) -> BenchmarkQueueMessage:
    message_id, fields = raw
    try:
        return BenchmarkQueueMessage(
            message_id=str(message_id),
            benchmark_sample_id=UUID(str(fields["benchmark_sample_id"])),
        )
    except (KeyError, ValueError, TypeError) as error:
        raise QueueUnavailableError("Benchmark queue message is malformed.") from error
