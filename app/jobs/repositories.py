"""PostgreSQL authority for job lifecycle, claims, completion, and outbox state."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.models import AIIdentity, AIStatus
from app.db.models import EvaluationJobRecord, EvaluationRecord, OutboxEventRecord
from app.db.repositories import PersistenceError, evaluation_record_from_snapshot
from app.evaluator.models import ScoreBreakdown
from app.jobs.models import (
    EvaluationJob,
    EvaluationJobSummary,
    JobStatus,
    OutboxEvent,
)
from app.jobs.retry import FailureDecision, retry_delay_seconds
from app.jobs.state import ensure_transition
from app.snapshots.models import EvaluationSnapshot, ExecutionEnvironmentSnapshot

logger = logging.getLogger(__name__)
EVALUATION_REQUESTED = "evaluation.requested"


class IdempotencyConflictError(ValueError):
    pass


class EvaluationJobRepository(Protocol):
    async def create(self, job: EvaluationJob) -> tuple[EvaluationJob, bool]: ...

    async def get(self, evaluation_id: UUID) -> EvaluationJob | None: ...

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        task_id: str | None = None,
        language: str | None = None,
        minimum_score: float | None = None,
        maximum_score: float | None = None,
    ) -> list[EvaluationJobSummary]: ...

    async def claim(
        self, evaluation_id: UUID, worker_id: str, now: datetime, lease_seconds: float
    ) -> EvaluationJob | None: ...

    async def renew_lease(
        self, evaluation_id: UUID, worker_id: str, now: datetime, lease_seconds: float
    ) -> bool: ...

    async def complete(
        self, evaluation_id: UUID, worker_id: str, snapshot: EvaluationSnapshot, now: datetime
    ) -> bool: ...

    async def record_failure(
        self,
        evaluation_id: UUID,
        worker_id: str,
        decision: FailureDecision,
        now: datetime,
        retry_base_delay_seconds: float,
    ) -> JobStatus | None: ...

    async def recover_stale(
        self, now: datetime, retry_base_delay_seconds: float, limit: int = 100
    ) -> int: ...

    async def ready_outbox(self, now: datetime, limit: int = 100) -> Sequence[OutboxEvent]: ...

    async def mark_outbox_published(self, event_id: UUID, now: datetime) -> bool: ...

    async def mark_outbox_failed(
        self, event_id: UUID, now: datetime, retry_base_delay_seconds: float
    ) -> bool: ...


class SqlAlchemyEvaluationJobRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, job: EvaluationJob) -> tuple[EvaluationJob, bool]:
        if job.idempotency_key is not None:
            existing = await self._get_by_idempotency_key(job.idempotency_key)
            if existing is not None:
                return self._reuse(existing, job.request_fingerprint)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    session.add(_job_record(job))
                    session.add(_outbox_record(job.evaluation_id, job.created_at))
        except IntegrityError as error:
            if job.idempotency_key is not None:
                existing = await self._get_by_idempotency_key(job.idempotency_key)
                if existing is not None:
                    return self._reuse(existing, job.request_fingerprint)
            self._log_error("job creation failed", error, job.evaluation_id)
            raise PersistenceError("Evaluation job persistence is unavailable.") from error
        except SQLAlchemyError as error:
            self._log_error("job creation failed", error, job.evaluation_id)
            raise PersistenceError("Evaluation job persistence is unavailable.") from error
        return job, True

    async def get(self, evaluation_id: UUID) -> EvaluationJob | None:
        try:
            async with self._session_factory() as session:
                record = await session.get(EvaluationJobRecord, evaluation_id)
        except SQLAlchemyError as error:
            self._log_error("job lookup failed", error, evaluation_id)
            raise PersistenceError("Evaluation job persistence is unavailable.") from error
        return None if record is None else _job_from_record(record)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        task_id: str | None = None,
        language: str | None = None,
        minimum_score: float | None = None,
        maximum_score: float | None = None,
    ) -> list[EvaluationJobSummary]:
        requested_rows = limit + offset
        query = select(
            EvaluationJobRecord,
            EvaluationRecord.final_score,
            EvaluationRecord.correctness_score,
            EvaluationRecord.code_quality_score,
            EvaluationRecord.type_safety_score,
            EvaluationRecord.security_score,
            EvaluationRecord.complexity_score,
            EvaluationRecord.ai_status,
            EvaluationRecord.ai_score,
        ).outerjoin(
            EvaluationRecord,
            EvaluationRecord.evaluation_id == EvaluationJobRecord.evaluation_id,
        )
        if task_id is not None:
            query = query.where(EvaluationJobRecord.task_id == task_id)
        if language is not None:
            query = query.where(EvaluationJobRecord.language == language)
        if minimum_score is not None:
            query = query.where(EvaluationRecord.final_score >= minimum_score)
        if maximum_score is not None:
            query = query.where(EvaluationRecord.final_score <= maximum_score)
        query = query.order_by(
            EvaluationJobRecord.created_at.desc(), EvaluationJobRecord.evaluation_id.desc()
        ).limit(requested_rows)
        legacy_query = (
            select(
                EvaluationRecord.evaluation_id,
                EvaluationRecord.created_at,
                EvaluationRecord.task_id,
                EvaluationRecord.task_version,
                EvaluationRecord.language,
                EvaluationRecord.source_hash,
                EvaluationRecord.final_score,
                EvaluationRecord.correctness_score,
                EvaluationRecord.code_quality_score,
                EvaluationRecord.type_safety_score,
                EvaluationRecord.security_score,
                EvaluationRecord.complexity_score,
                EvaluationRecord.ai_status,
                EvaluationRecord.ai_score,
            )
            .outerjoin(
                EvaluationJobRecord,
                EvaluationJobRecord.evaluation_id == EvaluationRecord.evaluation_id,
            )
            .where(EvaluationJobRecord.evaluation_id.is_(None))
        )
        if task_id is not None:
            legacy_query = legacy_query.where(EvaluationRecord.task_id == task_id)
        if language is not None:
            legacy_query = legacy_query.where(EvaluationRecord.language == language)
        if minimum_score is not None:
            legacy_query = legacy_query.where(EvaluationRecord.final_score >= minimum_score)
        if maximum_score is not None:
            legacy_query = legacy_query.where(EvaluationRecord.final_score <= maximum_score)
        legacy_query = legacy_query.order_by(
            EvaluationRecord.created_at.desc(), EvaluationRecord.evaluation_id.desc()
        ).limit(requested_rows)
        try:
            async with self._session_factory() as session:
                rows = (await session.execute(query)).all()
                legacy_rows = (await session.execute(legacy_query)).all()
        except SQLAlchemyError as error:
            self._log_error("job list failed", error)
            raise PersistenceError("Evaluation job persistence is unavailable.") from error
        summaries: list[EvaluationJobSummary] = []
        for row in rows:
            record = row[0]
            breakdown = None
            if row.final_score is not None:
                breakdown = ScoreBreakdown(
                    correctness=row.correctness_score,
                    code_quality=row.code_quality_score,
                    type_safety=row.type_safety_score,
                    security=row.security_score,
                    complexity=row.complexity_score,
                )
            summaries.append(
                EvaluationJobSummary(
                    evaluation_id=record.evaluation_id,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    task_id=record.task_id,
                    task_version=record.task_version,
                    language=record.language,
                    source_hash=record.source_hash,
                    status=JobStatus(record.status),
                    attempt_count=record.attempt_count,
                    score=row.final_score,
                    score_breakdown=breakdown,
                    ai_status=None if row.ai_status is None else AIStatus(row.ai_status),
                    ai_score=row.ai_score,
                )
            )
        for row in legacy_rows:
            summaries.append(
                EvaluationJobSummary(
                    evaluation_id=row.evaluation_id,
                    created_at=row.created_at,
                    updated_at=row.created_at,
                    task_id=row.task_id,
                    task_version=row.task_version,
                    language=row.language,
                    source_hash=row.source_hash,
                    status=JobStatus.COMPLETED,
                    attempt_count=0,
                    score=row.final_score,
                    score_breakdown=ScoreBreakdown(
                        correctness=row.correctness_score,
                        code_quality=row.code_quality_score,
                        type_safety=row.type_safety_score,
                        security=row.security_score,
                        complexity=row.complexity_score,
                    ),
                    ai_status=None if row.ai_status is None else AIStatus(row.ai_status),
                    ai_score=row.ai_score,
                )
            )
        summaries.sort(
            key=lambda item: (item.created_at, item.evaluation_id),
            reverse=True,
        )
        return summaries[offset : offset + limit]

    async def claim(
        self, evaluation_id: UUID, worker_id: str, now: datetime, lease_seconds: float
    ) -> EvaluationJob | None:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = await session.scalar(
                        select(EvaluationJobRecord)
                        .where(EvaluationJobRecord.evaluation_id == evaluation_id)
                        .with_for_update()
                    )
                    if record is None:
                        return None
                    current_status = JobStatus(record.status)
                    if current_status is JobStatus.RETRY_WAIT:
                        if record.next_attempt_at is None or record.next_attempt_at > now:
                            return None
                        ensure_transition(JobStatus.RETRY_WAIT, JobStatus.QUEUED)
                        record.status = JobStatus.QUEUED
                        record.queued_at = now
                        current_status = JobStatus.QUEUED
                    if current_status is not JobStatus.QUEUED:
                        return None
                    ensure_transition(JobStatus.QUEUED, JobStatus.RUNNING)
                    record.status = JobStatus.RUNNING
                    record.attempt_count += 1
                    record.started_at = now
                    record.updated_at = now
                    record.worker_id = worker_id
                    record.lease_expires_at = now + timedelta(seconds=lease_seconds)
                    record.next_attempt_at = None
                return _job_from_record(record)
        except SQLAlchemyError as error:
            self._log_error("job claim failed", error, evaluation_id)
            raise PersistenceError("Evaluation job persistence is unavailable.") from error

    async def renew_lease(
        self, evaluation_id: UUID, worker_id: str, now: datetime, lease_seconds: float
    ) -> bool:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = await session.scalar(
                        select(EvaluationJobRecord)
                        .where(EvaluationJobRecord.evaluation_id == evaluation_id)
                        .with_for_update()
                    )
                    if (
                        record is None
                        or JobStatus(record.status) is not JobStatus.RUNNING
                        or record.worker_id != worker_id
                    ):
                        return False
                    record.lease_expires_at = now + timedelta(seconds=lease_seconds)
                    record.updated_at = now
            return True
        except SQLAlchemyError as error:
            self._log_error("lease renewal failed", error, evaluation_id)
            raise PersistenceError("Evaluation job persistence is unavailable.") from error

    async def complete(
        self, evaluation_id: UUID, worker_id: str, snapshot: EvaluationSnapshot, now: datetime
    ) -> bool:
        if snapshot.evaluation_id != evaluation_id:
            raise ValueError("Snapshot evaluation identity does not match the job")
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = await session.scalar(
                        select(EvaluationJobRecord)
                        .where(EvaluationJobRecord.evaluation_id == evaluation_id)
                        .with_for_update()
                    )
                    if record is None:
                        return False
                    if JobStatus(record.status) is JobStatus.COMPLETED:
                        return True
                    if (
                        JobStatus(record.status) is not JobStatus.RUNNING
                        or record.worker_id != worker_id
                    ):
                        return False
                    ensure_transition(JobStatus.RUNNING, JobStatus.COMPLETED)
                    session.add(evaluation_record_from_snapshot(snapshot))
                    record.status = JobStatus.COMPLETED
                    record.completed_at = now
                    record.updated_at = now
                    record.snapshot_created = True
                    record.worker_id = None
                    record.lease_expires_at = None
                    record.last_error_category = None
                    record.last_error_code = None
            return True
        except IntegrityError as error:
            existing = await self.get(evaluation_id)
            if existing is not None and existing.status is JobStatus.COMPLETED:
                return True
            self._log_error("job completion failed", error, evaluation_id)
            raise PersistenceError("Evaluation completion persistence is unavailable.") from error
        except SQLAlchemyError as error:
            self._log_error("job completion failed", error, evaluation_id)
            raise PersistenceError("Evaluation completion persistence is unavailable.") from error

    async def record_failure(
        self,
        evaluation_id: UUID,
        worker_id: str,
        decision: FailureDecision,
        now: datetime,
        retry_base_delay_seconds: float,
    ) -> JobStatus | None:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = await session.scalar(
                        select(EvaluationJobRecord)
                        .where(EvaluationJobRecord.evaluation_id == evaluation_id)
                        .with_for_update()
                    )
                    if (
                        record is None
                        or JobStatus(record.status) is not JobStatus.RUNNING
                        or record.worker_id != worker_id
                    ):
                        return None
                    record.last_error_category = decision.category
                    record.last_error_code = decision.code
                    record.worker_id = None
                    record.lease_expires_at = None
                    record.updated_at = now
                    if decision.retryable and record.attempt_count < record.max_attempts:
                        ensure_transition(JobStatus.RUNNING, JobStatus.RETRY_WAIT)
                        delay = retry_delay_seconds(record.attempt_count, retry_base_delay_seconds)
                        next_attempt = now + timedelta(seconds=delay)
                        record.status = JobStatus.RETRY_WAIT
                        record.next_attempt_at = next_attempt
                        session.add(_outbox_record(evaluation_id, now, next_attempt))
                    else:
                        ensure_transition(JobStatus.RUNNING, JobStatus.FAILED)
                        record.status = JobStatus.FAILED
                        record.failed_at = now
                        record.next_attempt_at = None
                return JobStatus(record.status)
        except SQLAlchemyError as error:
            self._log_error("job failure transition failed", error, evaluation_id)
            raise PersistenceError("Evaluation job persistence is unavailable.") from error

    async def recover_stale(
        self, now: datetime, retry_base_delay_seconds: float, limit: int = 100
    ) -> int:
        recovered = 0
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    records = list(
                        await session.scalars(
                            select(EvaluationJobRecord)
                            .where(
                                EvaluationJobRecord.status == JobStatus.RUNNING,
                                EvaluationJobRecord.lease_expires_at < now,
                            )
                            .order_by(EvaluationJobRecord.lease_expires_at)
                            .limit(limit)
                            .with_for_update(skip_locked=True)
                        )
                    )
                    for record in records:
                        record.worker_id = None
                        record.lease_expires_at = None
                        record.updated_at = now
                        record.last_error_category = "infrastructure"
                        record.last_error_code = "worker_lease_expired"
                        if record.attempt_count < record.max_attempts:
                            ensure_transition(JobStatus.RUNNING, JobStatus.RETRY_WAIT)
                            delay = retry_delay_seconds(
                                record.attempt_count, retry_base_delay_seconds
                            )
                            next_attempt = now + timedelta(seconds=delay)
                            record.status = JobStatus.RETRY_WAIT
                            record.next_attempt_at = next_attempt
                            session.add(_outbox_record(record.evaluation_id, now, next_attempt))
                        else:
                            ensure_transition(JobStatus.RUNNING, JobStatus.FAILED)
                            record.status = JobStatus.FAILED
                            record.failed_at = now
                            record.next_attempt_at = None
                        recovered += 1
        except SQLAlchemyError as error:
            self._log_error("stale job recovery failed", error)
            raise PersistenceError("Evaluation job persistence is unavailable.") from error
        return recovered

    async def ready_outbox(self, now: datetime, limit: int = 100) -> Sequence[OutboxEvent]:
        try:
            async with self._session_factory() as session:
                records = list(
                    await session.scalars(
                        select(OutboxEventRecord)
                        .where(
                            OutboxEventRecord.published_at.is_(None),
                            OutboxEventRecord.next_attempt_at <= now,
                        )
                        .order_by(OutboxEventRecord.created_at, OutboxEventRecord.event_id)
                        .limit(limit)
                    )
                )
        except SQLAlchemyError as error:
            self._log_error("outbox lookup failed", error)
            raise PersistenceError("Outbox persistence is unavailable.") from error
        return [_outbox_from_record(record) for record in records]

    async def mark_outbox_published(self, event_id: UUID, now: datetime) -> bool:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    event = await session.scalar(
                        select(OutboxEventRecord)
                        .where(OutboxEventRecord.event_id == event_id)
                        .with_for_update()
                    )
                    if event is None:
                        return False
                    if event.published_at is not None:
                        return True
                    event.published_at = now
                    event.attempt_count += 1
                    event.last_error_code = None
            return True
        except SQLAlchemyError as error:
            self._log_error("outbox publication update failed", error)
            raise PersistenceError("Outbox persistence is unavailable.") from error

    async def mark_outbox_failed(
        self, event_id: UUID, now: datetime, retry_base_delay_seconds: float
    ) -> bool:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    event = await session.scalar(
                        select(OutboxEventRecord)
                        .where(OutboxEventRecord.event_id == event_id)
                        .with_for_update()
                    )
                    if event is None or event.published_at is not None:
                        return False
                    event.attempt_count += 1
                    event.last_error_code = "queue_unavailable"
                    delay = retry_delay_seconds(event.attempt_count, retry_base_delay_seconds)
                    event.next_attempt_at = now + timedelta(seconds=delay)
            return True
        except SQLAlchemyError as error:
            self._log_error("outbox failure update failed", error)
            raise PersistenceError("Outbox persistence is unavailable.") from error

    async def _get_by_idempotency_key(self, key: str) -> EvaluationJob | None:
        try:
            async with self._session_factory() as session:
                record = await session.scalar(
                    select(EvaluationJobRecord).where(EvaluationJobRecord.idempotency_key == key)
                )
        except SQLAlchemyError as error:
            self._log_error("idempotency lookup failed", error)
            raise PersistenceError("Evaluation job persistence is unavailable.") from error
        return None if record is None else _job_from_record(record)

    @staticmethod
    def _reuse(existing: EvaluationJob, request_identity: str) -> tuple[EvaluationJob, bool]:
        if existing.request_fingerprint != request_identity:
            raise IdempotencyConflictError(
                "Idempotency-Key was already used for a different evaluation request."
            )
        return existing, False

    @staticmethod
    def _log_error(message: str, error: SQLAlchemyError, evaluation_id: UUID | None = None) -> None:
        logger.error(
            "%s evaluation_id=%s error_type=%s",
            message,
            evaluation_id,
            type(error).__name__,
        )


def _job_record(job: EvaluationJob) -> EvaluationJobRecord:
    return EvaluationJobRecord(
        evaluation_id=job.evaluation_id,
        created_at=job.created_at,
        updated_at=job.updated_at,
        task_id=job.task_id,
        task_version=job.task_version,
        task_fingerprint=job.task_fingerprint,
        tests_fingerprint=job.tests_fingerprint,
        language=job.language,
        source_text=job.source_text,
        source_hash=job.source_hash,
        source_size=job.source_size,
        request_fingerprint=job.request_fingerprint,
        idempotency_key=job.idempotency_key,
        status=job.status,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        queued_at=job.queued_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        failed_at=job.failed_at,
        next_attempt_at=job.next_attempt_at,
        worker_id=job.worker_id,
        lease_expires_at=job.lease_expires_at,
        last_error_category=job.last_error_category,
        last_error_code=job.last_error_code,
        snapshot_created=job.snapshot_created,
        expected_execution=job.expected_execution.model_dump(mode="json"),
        expected_analyzer_versions=job.expected_analyzer_versions,
        expected_scoring_policy_version=job.expected_scoring_policy_version,
        expected_codejudge_version=job.expected_codejudge_version,
        expected_ai_identity=job.expected_ai_identity.model_dump(mode="json"),
    )


def _job_from_record(record: EvaluationJobRecord) -> EvaluationJob:
    return EvaluationJob(
        evaluation_id=record.evaluation_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        task_id=record.task_id,
        task_version=record.task_version,
        task_fingerprint=record.task_fingerprint,
        tests_fingerprint=record.tests_fingerprint,
        language=record.language,
        source_text=record.source_text,
        source_hash=record.source_hash,
        source_size=record.source_size,
        request_fingerprint=record.request_fingerprint,
        idempotency_key=record.idempotency_key,
        status=JobStatus(record.status),
        attempt_count=record.attempt_count,
        max_attempts=record.max_attempts,
        queued_at=record.queued_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        failed_at=record.failed_at,
        next_attempt_at=record.next_attempt_at,
        worker_id=record.worker_id,
        lease_expires_at=record.lease_expires_at,
        last_error_category=record.last_error_category,
        last_error_code=record.last_error_code,
        snapshot_created=record.snapshot_created,
        expected_execution=ExecutionEnvironmentSnapshot.model_validate(record.expected_execution),
        expected_analyzer_versions=record.expected_analyzer_versions,
        expected_scoring_policy_version=record.expected_scoring_policy_version,
        expected_codejudge_version=record.expected_codejudge_version,
        expected_ai_identity=AIIdentity.model_validate(record.expected_ai_identity),
    )


def _outbox_record(
    evaluation_id: UUID, created_at: datetime, next_attempt_at: datetime | None = None
) -> OutboxEventRecord:
    return OutboxEventRecord(
        event_id=uuid4(),
        event_type=EVALUATION_REQUESTED,
        aggregate_id=evaluation_id,
        payload={"evaluation_id": str(evaluation_id)},
        created_at=created_at,
        published_at=None,
        attempt_count=0,
        next_attempt_at=next_attempt_at or created_at,
        last_error_code=None,
    )


def _outbox_from_record(record: OutboxEventRecord) -> OutboxEvent:
    return OutboxEvent(
        event_id=record.event_id,
        aggregate_id=record.aggregate_id,
        event_type=record.event_type,
        created_at=record.created_at,
        published_at=record.published_at,
        attempt_count=record.attempt_count,
        next_attempt_at=record.next_attempt_at,
    )
