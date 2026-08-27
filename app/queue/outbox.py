"""Transactional-outbox publisher from PostgreSQL to Redis Streams."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from app.jobs.repositories import EvaluationJobRepository
from app.jobs.service import utc_now
from app.queue.redis_streams import EvaluationQueue, QueueUnavailableError

logger = logging.getLogger(__name__)


class OutboxPublisher:
    def __init__(
        self,
        repository: EvaluationJobRepository,
        queue: EvaluationQueue,
        *,
        retry_base_delay_seconds: float,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._clock = clock

    async def dispatch_once(self, limit: int = 100) -> int:
        events = await self._repository.ready_outbox(self._clock(), limit=limit)
        published = 0
        for event in events:
            try:
                await self._queue.enqueue(event.aggregate_id)
            except QueueUnavailableError:
                logger.warning(
                    "outbox publication deferred evaluation_id=%s event_id=%s",
                    event.aggregate_id,
                    event.event_id,
                )
                await self._repository.mark_outbox_failed(
                    event.event_id,
                    self._clock(),
                    self._retry_base_delay_seconds,
                )
                continue
            await self._repository.mark_outbox_published(event.event_id, self._clock())
            published += 1
        return published
