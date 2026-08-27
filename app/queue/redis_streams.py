"""Focused Redis Streams queue with explicit at-least-once semantics."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable
from typing import Any, Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

DEFAULT_STREAM = "codejudge:evaluations"
DEFAULT_GROUP = "codejudge-workers"
_HEARTBEAT_PREFIX = "codejudge:worker:"


class QueueUnavailableError(RuntimeError):
    pass


class QueueMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    message_id: str
    evaluation_id: UUID


class EvaluationQueue(Protocol):
    async def ensure_group(self) -> None: ...

    async def enqueue(self, evaluation_id: UUID) -> str: ...

    async def consume(self, consumer: str, block_ms: int = 1000) -> QueueMessage | None: ...

    async def reclaim(self, consumer: str, minimum_idle_ms: int) -> QueueMessage | None: ...

    async def acknowledge(self, message_id: str) -> None: ...

    async def check_capability(self) -> bool: ...

    async def heartbeat(self, worker_id: str, ttl_seconds: int) -> None: ...

    async def active_workers(self) -> int: ...

    async def close(self) -> None: ...


class RedisStreamsQueue:
    def __init__(
        self,
        redis_url: str,
        *,
        stream: str = DEFAULT_STREAM,
        group: str = DEFAULT_GROUP,
    ) -> None:
        self._redis: Redis = Redis.from_url(redis_url, decode_responses=True)
        self.stream = stream
        self.group = group

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise QueueUnavailableError("Queue consumer group is unavailable.") from error
        except RedisError as error:
            raise QueueUnavailableError("Queue is unavailable.") from error

    async def enqueue(self, evaluation_id: UUID) -> str:
        try:
            message_id = await self._redis.xadd(
                self.stream,
                {"evaluation_id": str(evaluation_id)},
            )
        except RedisError as error:
            raise QueueUnavailableError("Queue is unavailable.") from error
        return str(message_id)

    async def consume(self, consumer: str, block_ms: int = 1000) -> QueueMessage | None:
        try:
            response: Any = await self._redis.xreadgroup(
                self.group,
                consumer,
                {self.stream: ">"},
                count=1,
                block=block_ms,
            )
        except RedisError as error:
            raise QueueUnavailableError("Queue is unavailable.") from error
        if not response:
            return None
        _, messages = response[0]
        return _parse_message(messages[0])

    async def reclaim(self, consumer: str, minimum_idle_ms: int) -> QueueMessage | None:
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
            raise QueueUnavailableError("Queue is unavailable.") from error
        messages = response[1] if response and len(response) > 1 else []
        return None if not messages else _parse_message(messages[0])

    async def acknowledge(self, message_id: str) -> None:
        try:
            await self._redis.xack(self.stream, self.group, message_id)
        except RedisError as error:
            raise QueueUnavailableError("Queue acknowledgement failed.") from error

    async def check_capability(self) -> bool:
        try:
            result = await cast(Awaitable[bool], self._redis.ping())
            return bool(result)
        except RedisError:
            return False

    async def heartbeat(self, worker_id: str, ttl_seconds: int) -> None:
        try:
            await self._redis.set(
                f"{_HEARTBEAT_PREFIX}{worker_id}",
                "alive",
                ex=max(1, ttl_seconds),
            )
        except RedisError as error:
            raise QueueUnavailableError("Worker heartbeat failed.") from error

    async def active_workers(self) -> int:
        try:
            workers: AsyncIterator[str] = self._redis.scan_iter(match=f"{_HEARTBEAT_PREFIX}*")
            count = 0
            async for _ in workers:
                count += 1
            return count
        except RedisError as error:
            raise QueueUnavailableError("Worker heartbeat registry is unavailable.") from error

    async def close(self) -> None:
        await self._redis.aclose()


def _parse_message(raw: Any) -> QueueMessage:
    message_id, fields = raw
    try:
        return QueueMessage(
            message_id=str(message_id),
            evaluation_id=UUID(str(fields["evaluation_id"])),
        )
    except (KeyError, ValueError, TypeError) as error:
        raise QueueUnavailableError("Queue message is malformed.") from error
