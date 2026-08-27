from datetime import UTC, datetime
from uuid import uuid4

from app.jobs.models import OutboxEvent
from app.queue.outbox import OutboxPublisher
from app.queue.redis_streams import QueueUnavailableError


class OutboxRepository:
    def __init__(self, event: OutboxEvent) -> None:
        self.event = event
        self.published = False
        self.failed = False

    async def ready_outbox(self, now: datetime, limit: int = 100) -> list[OutboxEvent]:
        del now, limit
        return [] if self.published else [self.event]

    async def mark_outbox_published(self, event_id: object, now: datetime) -> bool:
        del event_id, now
        self.published = True
        return True

    async def mark_outbox_failed(
        self, event_id: object, now: datetime, retry_base_delay_seconds: float
    ) -> bool:
        del event_id, now, retry_base_delay_seconds
        self.failed = True
        return True


class Queue:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[object] = []

    async def enqueue(self, evaluation_id: object) -> str:
        if self.fail:
            raise QueueUnavailableError("offline")
        self.messages.append(evaluation_id)
        return "1-0"


def _event() -> OutboxEvent:
    now = datetime(2026, 8, 27, tzinfo=UTC)
    return OutboxEvent(
        event_id=uuid4(),
        aggregate_id=uuid4(),
        event_type="evaluation.requested",
        created_at=now,
        attempt_count=0,
        next_attempt_at=now,
    )


async def test_outbox_publishes_identity_then_marks_event() -> None:
    event = _event()
    repository = OutboxRepository(event)
    queue = Queue()
    publisher = OutboxPublisher(repository, queue, retry_base_delay_seconds=5)

    assert await publisher.dispatch_once() == 1
    assert queue.messages == [event.aggregate_id]
    assert repository.published is True


async def test_queue_failure_leaves_event_retryable() -> None:
    event = _event()
    repository = OutboxRepository(event)
    publisher = OutboxPublisher(repository, Queue(fail=True), retry_base_delay_seconds=5)

    assert await publisher.dispatch_once() == 0
    assert repository.published is False
    assert repository.failed is True
