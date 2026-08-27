from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.jobs.models import JobStatus
from app.jobs.repositories import IdempotencyConflictError
from app.jobs.retry import FailureDecision
from tests.database.conftest import DatabaseHarness
from tests.database.helpers import job_fixture, snapshot_fixture

pytestmark = pytest.mark.database


async def test_job_and_outbox_are_created_transactionally(
    database_harness: DatabaseHarness,
) -> None:
    job = job_fixture(idempotency_key="request-1")

    stored, created = await database_harness.job_repository.create(job)
    outbox = await database_harness.job_repository.ready_outbox(job.created_at)

    assert created is True
    assert stored == job
    assert await database_harness.job_repository.get(job.evaluation_id) == job
    assert len(outbox) == 1
    assert outbox[0].aggregate_id == job.evaluation_id


async def test_idempotency_key_reuses_only_identical_request(
    database_harness: DatabaseHarness,
) -> None:
    job = job_fixture(idempotency_key="stable-key")
    await database_harness.job_repository.create(job)

    reused, created = await database_harness.job_repository.create(
        job_fixture(source=job.source_text, idempotency_key="stable-key")
    )

    assert created is False
    assert reused.evaluation_id == job.evaluation_id
    with pytest.raises(IdempotencyConflictError):
        await database_harness.job_repository.create(
            job_fixture(
                source="class LRUCache:\n    changed = True\n", idempotency_key="stable-key"
            )
        )


async def test_without_idempotency_key_identical_jobs_remain_distinct(
    database_harness: DatabaseHarness,
) -> None:
    first = job_fixture()
    second = job_fixture()

    await database_harness.job_repository.create(first)
    await database_harness.job_repository.create(second)

    assert first.evaluation_id != second.evaluation_id
    assert first.request_fingerprint == second.request_fingerprint


async def test_only_one_worker_can_claim_a_job(database_harness: DatabaseHarness) -> None:
    job = job_fixture()
    await database_harness.job_repository.create(job)

    first, second = await asyncio.gather(
        database_harness.job_repository.claim(job.evaluation_id, "worker-a", job.created_at, 60),
        database_harness.job_repository.claim(job.evaluation_id, "worker-b", job.created_at, 60),
    )

    claims = [claim for claim in (first, second) if claim is not None]
    assert len(claims) == 1
    assert claims[0].status is JobStatus.RUNNING
    assert claims[0].attempt_count == 1


async def test_completion_inserts_snapshot_and_terminal_state_atomically(
    database_harness: DatabaseHarness,
) -> None:
    job = job_fixture()
    await database_harness.job_repository.create(job)
    claimed = await database_harness.job_repository.claim(
        job.evaluation_id, "worker", job.created_at, 60
    )
    assert claimed is not None
    snapshot = snapshot_fixture(
        source=job.source_text,
        created_at=job.created_at,
        evaluation_id=job.evaluation_id,
    )

    completed = await database_harness.job_repository.complete(
        job.evaluation_id,
        "worker",
        snapshot,
        job.created_at + timedelta(seconds=1),
    )
    stored_job = await database_harness.job_repository.get(job.evaluation_id)
    stored_snapshot = await database_harness.repository.get(job.evaluation_id)

    assert completed is True
    assert stored_job is not None
    assert stored_job.status is JobStatus.COMPLETED
    assert stored_job.snapshot_created is True
    assert stored_snapshot == snapshot
    assert (
        await database_harness.job_repository.claim(
            job.evaluation_id, "another-worker", job.created_at, 60
        )
        is None
    )


async def test_phase4_snapshot_without_job_remains_visible_in_phase5_history(
    database_harness: DatabaseHarness,
) -> None:
    snapshot = snapshot_fixture()
    await database_harness.repository.create(snapshot)

    history = await database_harness.job_repository.list(limit=10, offset=0)

    assert [item.evaluation_id for item in history] == [snapshot.evaluation_id]
    assert history[0].status is JobStatus.COMPLETED
    assert history[0].score == snapshot.final_score


async def test_retry_wait_is_durable_and_outbox_publication_requeues(
    database_harness: DatabaseHarness,
) -> None:
    job = job_fixture()
    await database_harness.job_repository.create(job)
    initial_event = (await database_harness.job_repository.ready_outbox(job.created_at))[0]
    await database_harness.job_repository.mark_outbox_published(
        initial_event.event_id, job.created_at
    )
    await database_harness.job_repository.claim(job.evaluation_id, "worker", job.created_at, 60)
    failure_time = job.created_at + timedelta(seconds=1)

    status = await database_harness.job_repository.record_failure(
        job.evaluation_id,
        "worker",
        FailureDecision(True, "infrastructure", "sandbox_unavailable"),
        failure_time,
        5,
    )
    waiting = await database_harness.job_repository.get(job.evaluation_id)

    assert status is JobStatus.RETRY_WAIT
    assert waiting is not None
    assert waiting.next_attempt_at == failure_time + timedelta(seconds=5)
    assert await database_harness.job_repository.ready_outbox(failure_time) == []
    retry_event = (
        await database_harness.job_repository.ready_outbox(failure_time + timedelta(seconds=5))
    )[0]
    retry_time = failure_time + timedelta(seconds=5)
    await database_harness.job_repository.mark_outbox_published(retry_event.event_id, retry_time)
    still_waiting = await database_harness.job_repository.get(job.evaluation_id)
    assert still_waiting is not None
    assert still_waiting.status is JobStatus.RETRY_WAIT
    claimed = await database_harness.job_repository.claim(
        job.evaluation_id, "retry-worker", retry_time, 60
    )
    assert claimed is not None
    assert claimed.status is JobStatus.RUNNING
    assert claimed.attempt_count == 2


async def test_stale_lease_recovery_respects_active_and_attempt_limit(
    database_harness: DatabaseHarness,
) -> None:
    now = datetime(2026, 8, 27, 11, tzinfo=UTC)
    retryable = job_fixture(created_at=now)
    exhausted = job_fixture(created_at=now, max_attempts=1)
    for job in (retryable, exhausted):
        await database_harness.job_repository.create(job)
        await database_harness.job_repository.claim(job.evaluation_id, "dead-worker", now, 60)

    assert await database_harness.job_repository.recover_stale(now + timedelta(seconds=30), 5) == 0
    assert await database_harness.job_repository.recover_stale(now + timedelta(seconds=61), 5) == 2
    retryable_after = await database_harness.job_repository.get(retryable.evaluation_id)
    exhausted_after = await database_harness.job_repository.get(exhausted.evaluation_id)

    assert retryable_after is not None
    assert retryable_after.status is JobStatus.RETRY_WAIT
    assert exhausted_after is not None
    assert exhausted_after.status is JobStatus.FAILED
    assert exhausted_after.last_error_code == "worker_lease_expired"
