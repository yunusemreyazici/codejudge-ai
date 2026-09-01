"""PostgreSQL authority for benchmark plans, artifacts, lifecycle, and aggregation rows."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.benchmarks.models import (
    TERMINAL_SAMPLE_STATUSES,
    BenchmarkModelConfig,
    BenchmarkRun,
    BenchmarkRunStatus,
    BenchmarkSample,
    BenchmarkSampleStatus,
    GeneratedSolutionArtifact,
    GenerationOutputMode,
    PricingSnapshot,
)
from app.db.models import (
    BenchmarkGenerationArtifactRecord,
    BenchmarkModelConfigRecord,
    BenchmarkOutboxEventRecord,
    BenchmarkRunRecord,
    BenchmarkSampleRecord,
    EvaluationRecord,
)
from app.db.repositories import PersistenceError, evaluation_record_from_snapshot
from app.jobs.models import OutboxEvent
from app.jobs.repositories import IdempotencyConflictError
from app.jobs.retry import retry_delay_seconds
from app.snapshots.models import EvaluationSnapshot

logger = logging.getLogger(__name__)
BENCHMARK_SAMPLE_REQUESTED = "benchmark.sample.requested"


@dataclass(frozen=True, slots=True)
class BenchmarkResultRow:
    sample: BenchmarkSample
    config: BenchmarkModelConfig
    artifact: GeneratedSolutionArtifact | None
    deterministic_score: float | None
    ai_score: float | None
    judge_score: float | None
    adversarial_robustness: float | None
    ai_status: str | None
    tests_passed: int | None = None
    tests_failed: int | None = None
    test_execution_seconds: float | None = None
    evaluation_lifecycle_seconds: float | None = None

    @property
    def benchmark_sample_id(self) -> UUID:
        return self.sample.benchmark_sample_id

    @property
    def model_config_id(self) -> UUID:
        return self.sample.model_config_id

    @property
    def task_id(self) -> str:
        return self.sample.task_id

    @property
    def task_weight(self) -> float:
        return self.sample.task_weight

    @property
    def status(self) -> BenchmarkSampleStatus:
        return self.sample.status

    @property
    def generation_latency_ms(self) -> int | None:
        return None if self.artifact is None else self.artifact.generation_latency_ms

    @property
    def evaluation_duration_seconds(self) -> float | None:
        return self.sample.evaluation_duration_seconds

    @property
    def generation_cost(self) -> Decimal | None:
        return None if self.artifact is None else self.artifact.generation_cost

    @property
    def currency(self) -> str | None:
        return None if self.artifact is None else self.artifact.currency


@dataclass(frozen=True, slots=True)
class BenchmarkLeaseRenewal:
    renewed_at: datetime
    lease_expires_at: datetime


class BenchmarkRepository(Protocol):
    async def create_plan(
        self,
        run: BenchmarkRun,
        configs: Sequence[BenchmarkModelConfig],
        samples: Sequence[BenchmarkSample],
    ) -> tuple[BenchmarkRun, bool]: ...

    async def get_run(self, run_id: UUID) -> BenchmarkRun | None: ...

    async def list_runs(
        self,
        *,
        limit: int,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
    ) -> list[BenchmarkRun]: ...

    async def get_configs(self, run_id: UUID) -> list[BenchmarkModelConfig]: ...

    async def get_sample(self, sample_id: UUID) -> BenchmarkSample | None: ...

    async def get_artifact(self, sample_id: UUID) -> GeneratedSolutionArtifact | None: ...

    async def get_config(self, config_id: UUID) -> BenchmarkModelConfig | None: ...

    async def result_rows(
        self,
        run_id: UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
        model: str | None = None,
        task_id: str | None = None,
        status: BenchmarkSampleStatus | None = None,
    ) -> list[BenchmarkResultRow]: ...

    async def claim(
        self, sample_id: UUID, worker_id: str, now: datetime, lease_seconds: float
    ) -> BenchmarkSample | None: ...

    async def renew_lease(
        self, sample_id: UUID, worker_id: str, now: datetime, lease_seconds: float
    ) -> BenchmarkLeaseRenewal | None: ...

    async def store_artifact(
        self, sample_id: UUID, worker_id: str, artifact: GeneratedSolutionArtifact, now: datetime
    ) -> bool: ...

    async def complete(
        self,
        sample_id: UUID,
        worker_id: str,
        snapshot: EvaluationSnapshot,
        now: datetime,
        total_duration_seconds: float,
    ) -> bool: ...

    async def record_failure(
        self,
        sample_id: UUID,
        worker_id: str,
        code: str,
        *,
        generation: bool,
        retryable: bool,
        now: datetime,
        retry_base_delay_seconds: float,
    ) -> BenchmarkSampleStatus | None: ...

    async def recover_stale(
        self, now: datetime, retry_base_delay_seconds: float, limit: int = 100
    ) -> int: ...

    async def reconcile_terminal_runs(self, now: datetime, limit: int = 100) -> int: ...

    async def ready_outbox(self, now: datetime, limit: int = 100) -> Sequence[OutboxEvent]: ...

    async def mark_outbox_published(self, event_id: UUID, now: datetime) -> bool: ...

    async def mark_outbox_failed(
        self, event_id: UUID, now: datetime, retry_base_delay_seconds: float
    ) -> bool: ...


class SqlAlchemyBenchmarkRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_plan(
        self,
        run: BenchmarkRun,
        configs: Sequence[BenchmarkModelConfig],
        samples: Sequence[BenchmarkSample],
    ) -> tuple[BenchmarkRun, bool]:
        if run.idempotency_key is not None:
            existing = await self._get_by_idempotency_key(run.idempotency_key)
            if existing is not None:
                return self._reuse(existing, run.request_fingerprint)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    session.add(_run_record(run))
                    await session.flush()
                    session.add_all([_config_record(config) for config in configs])
                    await session.flush()
                    for sample in samples:
                        session.add(_sample_record(sample))
                    await session.flush()
                    for sample in samples:
                        session.add(_outbox_record(sample.benchmark_sample_id, sample.created_at))
        except IntegrityError as error:
            if run.idempotency_key is not None:
                existing = await self._get_by_idempotency_key(run.idempotency_key)
                if existing is not None:
                    return self._reuse(existing, run.request_fingerprint)
            raise PersistenceError("Benchmark persistence is unavailable.") from error
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark persistence is unavailable.") from error
        return run.model_copy(update={"model_configs": tuple(configs)}), True

    async def get_run(self, run_id: UUID) -> BenchmarkRun | None:
        try:
            async with self._session_factory() as session:
                record = await session.get(BenchmarkRunRecord, run_id)
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark persistence is unavailable.") from error
        if record is None:
            return None
        configs = await self.get_configs(run_id)
        return _run_from_record(record).model_copy(update={"model_configs": tuple(configs)})

    async def list_runs(
        self,
        *,
        limit: int,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
    ) -> list[BenchmarkRun]:
        query = select(BenchmarkRunRecord)
        if dataset_id is not None:
            query = query.where(BenchmarkRunRecord.dataset_id == dataset_id)
        if dataset_version is not None:
            query = query.where(BenchmarkRunRecord.dataset_version == dataset_version)
        query = query.order_by(
            BenchmarkRunRecord.created_at.desc(), BenchmarkRunRecord.benchmark_run_id.desc()
        ).limit(limit)
        try:
            async with self._session_factory() as session:
                records = list(await session.scalars(query))
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark persistence is unavailable.") from error
        runs: list[BenchmarkRun] = []
        for record in records:
            configs = await self.get_configs(record.benchmark_run_id)
            runs.append(
                _run_from_record(record).model_copy(update={"model_configs": tuple(configs)})
            )
        return runs

    async def get_configs(self, run_id: UUID) -> list[BenchmarkModelConfig]:
        try:
            async with self._session_factory() as session:
                records = list(
                    await session.scalars(
                        select(BenchmarkModelConfigRecord)
                        .where(BenchmarkModelConfigRecord.benchmark_run_id == run_id)
                        .order_by(BenchmarkModelConfigRecord.ordinal)
                    )
                )
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark persistence is unavailable.") from error
        return [_config_from_record(record) for record in records]

    async def get_config(self, config_id: UUID) -> BenchmarkModelConfig | None:
        try:
            async with self._session_factory() as session:
                record = await session.get(BenchmarkModelConfigRecord, config_id)
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark persistence is unavailable.") from error
        return None if record is None else _config_from_record(record)

    async def get_sample(self, sample_id: UUID) -> BenchmarkSample | None:
        try:
            async with self._session_factory() as session:
                record = await session.get(BenchmarkSampleRecord, sample_id)
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark persistence is unavailable.") from error
        return None if record is None else _sample_from_record(record)

    async def get_artifact(self, sample_id: UUID) -> GeneratedSolutionArtifact | None:
        try:
            async with self._session_factory() as session:
                record = await session.get(BenchmarkGenerationArtifactRecord, sample_id)
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark artifact persistence is unavailable.") from error
        return None if record is None else _artifact_from_record(record)

    async def result_rows(
        self,
        run_id: UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
        model: str | None = None,
        task_id: str | None = None,
        status: BenchmarkSampleStatus | None = None,
    ) -> list[BenchmarkResultRow]:
        query = (
            select(
                BenchmarkSampleRecord,
                BenchmarkModelConfigRecord,
                BenchmarkGenerationArtifactRecord,
                EvaluationRecord.final_score,
                EvaluationRecord.ai_score,
                EvaluationRecord.judge_score,
                EvaluationRecord.adversarial_robustness,
                EvaluationRecord.ai_status,
                EvaluationRecord.tests_passed,
                EvaluationRecord.tests_failed,
                EvaluationRecord.test_duration_seconds,
                EvaluationRecord.duration_seconds,
            )
            .join(
                BenchmarkModelConfigRecord,
                BenchmarkModelConfigRecord.model_config_id == BenchmarkSampleRecord.model_config_id,
            )
            .outerjoin(
                BenchmarkGenerationArtifactRecord,
                BenchmarkGenerationArtifactRecord.benchmark_sample_id
                == BenchmarkSampleRecord.benchmark_sample_id,
            )
            .outerjoin(
                EvaluationRecord,
                EvaluationRecord.evaluation_id == BenchmarkSampleRecord.evaluation_id,
            )
            .where(BenchmarkSampleRecord.benchmark_run_id == run_id)
        )
        if model is not None:
            query = query.where(BenchmarkModelConfigRecord.model == model)
        if task_id is not None:
            query = query.where(BenchmarkSampleRecord.task_id == task_id)
        if status is not None:
            query = query.where(BenchmarkSampleRecord.status == status)
        query = query.order_by(
            BenchmarkModelConfigRecord.ordinal,
            BenchmarkSampleRecord.task_id,
            BenchmarkSampleRecord.sample_index,
        ).offset(offset)
        if limit is not None:
            query = query.limit(limit)
        try:
            async with self._session_factory() as session:
                rows = (await session.execute(query)).all()
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark persistence is unavailable.") from error
        return [
            BenchmarkResultRow(
                sample=_sample_from_record(row[0]),
                config=_config_from_record(row[1]),
                artifact=None if row[2] is None else _artifact_from_record(row[2]),
                deterministic_score=row[3],
                ai_score=row[4],
                judge_score=row[5],
                adversarial_robustness=row[6],
                ai_status=row[7],
                tests_passed=row[8],
                tests_failed=row[9],
                test_execution_seconds=row[10],
                evaluation_lifecycle_seconds=row[11],
            )
            for row in rows
        ]

    async def claim(
        self, sample_id: UUID, worker_id: str, now: datetime, lease_seconds: float
    ) -> BenchmarkSample | None:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = await session.scalar(
                        select(BenchmarkSampleRecord)
                        .where(BenchmarkSampleRecord.benchmark_sample_id == sample_id)
                        .with_for_update()
                    )
                    if (
                        record is None
                        or BenchmarkSampleStatus(record.status) in TERMINAL_SAMPLE_STATUSES
                    ):
                        return None
                    if record.status not in {
                        BenchmarkSampleStatus.QUEUED,
                        BenchmarkSampleStatus.GENERATED,
                    }:
                        return None
                    artifact = await session.get(BenchmarkGenerationArtifactRecord, sample_id)
                    record.status = (
                        BenchmarkSampleStatus.EVALUATING
                        if artifact is not None
                        else BenchmarkSampleStatus.GENERATING
                    )
                    record.attempt_count += 1
                    record.worker_id = worker_id
                    record.lease_expires_at = now + timedelta(seconds=lease_seconds)
                    record.updated_at = now
                    run = await session.get(BenchmarkRunRecord, record.benchmark_run_id)
                    if run is not None and run.status == BenchmarkRunStatus.QUEUED:
                        run.status = BenchmarkRunStatus.RUNNING
                        run.started_at = now
                return _sample_from_record(record)
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark sample claim is unavailable.") from error

    async def renew_lease(
        self, sample_id: UUID, worker_id: str, now: datetime, lease_seconds: float
    ) -> BenchmarkLeaseRenewal | None:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = await session.get(
                        BenchmarkSampleRecord, sample_id, with_for_update=True
                    )
                    if (
                        record is None
                        or record.worker_id != worker_id
                        or record.lease_expires_at is None
                        or record.lease_expires_at <= now
                    ):
                        return None
                    if BenchmarkSampleStatus(record.status) not in {
                        BenchmarkSampleStatus.GENERATING,
                        BenchmarkSampleStatus.EVALUATING,
                    }:
                        return None
                    lease_expires_at = now + timedelta(seconds=lease_seconds)
                    record.lease_expires_at = lease_expires_at
                    record.updated_at = now
            return BenchmarkLeaseRenewal(
                renewed_at=now,
                lease_expires_at=lease_expires_at,
            )
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark lease renewal is unavailable.") from error

    async def store_artifact(
        self, sample_id: UUID, worker_id: str, artifact: GeneratedSolutionArtifact, now: datetime
    ) -> bool:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = await session.get(
                        BenchmarkSampleRecord, sample_id, with_for_update=True
                    )
                    if record is None or not _owns_active_benchmark_lease(record, worker_id, now):
                        return False
                    existing = await session.get(BenchmarkGenerationArtifactRecord, sample_id)
                    if existing is not None:
                        return existing.source_hash == artifact.source_hash
                    if BenchmarkSampleStatus(record.status) is not BenchmarkSampleStatus.GENERATING:
                        return False
                    session.add(_artifact_record(artifact))
                    record.status = BenchmarkSampleStatus.EVALUATING
                    record.updated_at = now
            return True
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark artifact persistence is unavailable.") from error

    async def complete(
        self,
        sample_id: UUID,
        worker_id: str,
        snapshot: EvaluationSnapshot,
        now: datetime,
        total_duration_seconds: float,
    ) -> bool:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = await session.get(
                        BenchmarkSampleRecord, sample_id, with_for_update=True
                    )
                    if record is None or snapshot.evaluation_id != record.evaluation_id:
                        return False
                    if BenchmarkSampleStatus(record.status) is BenchmarkSampleStatus.COMPLETED:
                        return True
                    if not _owns_active_benchmark_lease(record, worker_id, now):
                        return False
                    existing = await session.get(EvaluationRecord, snapshot.evaluation_id)
                    if existing is None:
                        session.add(evaluation_record_from_snapshot(snapshot))
                    elif existing.source_hash != snapshot.source_hash:
                        raise PersistenceError("Benchmark evaluation identity conflict.")
                    record.status = BenchmarkSampleStatus.COMPLETED
                    record.failure_code = None
                    record.evaluation_duration_seconds = snapshot.duration_seconds
                    record.total_duration_seconds = total_duration_seconds
                    record.completed_at = now
                    record.updated_at = now
                    record.worker_id = None
                    record.lease_expires_at = None
                    await self._finish_run_if_terminal(session, record.benchmark_run_id, now)
            return True
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark completion persistence is unavailable.") from error

    async def record_failure(
        self,
        sample_id: UUID,
        worker_id: str,
        code: str,
        *,
        generation: bool,
        retryable: bool,
        now: datetime,
        retry_base_delay_seconds: float,
    ) -> BenchmarkSampleStatus | None:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = await session.get(
                        BenchmarkSampleRecord, sample_id, with_for_update=True
                    )
                    if record is None or not _owns_active_benchmark_lease(record, worker_id, now):
                        return None
                    record.failure_code = code
                    record.worker_id = None
                    record.lease_expires_at = None
                    record.updated_at = now
                    if retryable and record.attempt_count < record.max_attempts:
                        record.status = (
                            BenchmarkSampleStatus.GENERATED
                            if await session.get(BenchmarkGenerationArtifactRecord, sample_id)
                            else BenchmarkSampleStatus.QUEUED
                        )
                        delay = retry_delay_seconds(record.attempt_count, retry_base_delay_seconds)
                        session.add(_outbox_record(sample_id, now, now + timedelta(seconds=delay)))
                    else:
                        record.status = (
                            BenchmarkSampleStatus.GENERATION_FAILED
                            if generation
                            else BenchmarkSampleStatus.EVALUATION_FAILED
                        )
                        record.completed_at = now
                        await self._finish_run_if_terminal(session, record.benchmark_run_id, now)
                return BenchmarkSampleStatus(record.status)
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark failure persistence is unavailable.") from error

    async def recover_stale(
        self, now: datetime, retry_base_delay_seconds: float, limit: int = 100
    ) -> int:
        recovered = 0
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    records = list(
                        await session.scalars(
                            select(BenchmarkSampleRecord)
                            .where(
                                BenchmarkSampleRecord.status.in_(
                                    [
                                        BenchmarkSampleStatus.GENERATING,
                                        BenchmarkSampleStatus.EVALUATING,
                                    ]
                                ),
                                BenchmarkSampleRecord.lease_expires_at < now,
                            )
                            .limit(limit)
                            .with_for_update(skip_locked=True)
                        )
                    )
                    for record in records:
                        previous_worker_id = record.worker_id
                        expired_at = record.lease_expires_at
                        artifact = await session.get(
                            BenchmarkGenerationArtifactRecord, record.benchmark_sample_id
                        )
                        record.worker_id = None
                        record.lease_expires_at = None
                        record.updated_at = now
                        record.failure_code = "worker_lease_expired"
                        if record.attempt_count < record.max_attempts:
                            record.status = (
                                BenchmarkSampleStatus.GENERATED
                                if artifact is not None
                                else BenchmarkSampleStatus.QUEUED
                            )
                            delay = retry_delay_seconds(
                                record.attempt_count, retry_base_delay_seconds
                            )
                            session.add(
                                _outbox_record(
                                    record.benchmark_sample_id,
                                    now,
                                    now + timedelta(seconds=delay),
                                )
                            )
                        else:
                            record.status = BenchmarkSampleStatus.SKIPPED
                            record.completed_at = now
                            await self._finish_run_if_terminal(
                                session, record.benchmark_run_id, now
                            )
                        logger.warning(
                            "benchmark lease expired run_id=%s sample_id=%s worker_id=%s "
                            "attempt=%d max_attempts=%d lease_expired_at=%s recovered_at=%s "
                            "terminal=%s ownership_lost=true",
                            record.benchmark_run_id,
                            record.benchmark_sample_id,
                            previous_worker_id,
                            record.attempt_count,
                            record.max_attempts,
                            expired_at,
                            now,
                            BenchmarkSampleStatus(record.status) is BenchmarkSampleStatus.SKIPPED,
                        )
                        recovered += 1
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark recovery persistence is unavailable.") from error
        return recovered

    async def reconcile_terminal_runs(self, now: datetime, limit: int = 100) -> int:
        """Repair derived run lifecycle after concurrent or interrupted terminal transitions."""
        finalized = 0
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    run_ids = list(
                        await session.scalars(
                            select(BenchmarkRunRecord.benchmark_run_id)
                            .where(
                                BenchmarkRunRecord.status.in_(
                                    [BenchmarkRunStatus.QUEUED, BenchmarkRunStatus.RUNNING]
                                )
                            )
                            .order_by(BenchmarkRunRecord.created_at)
                            .limit(limit)
                        )
                    )
                    for run_id in run_ids:
                        finalized += int(await self._finish_run_if_terminal(session, run_id, now))
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark run reconciliation is unavailable.") from error
        return finalized

    async def ready_outbox(self, now: datetime, limit: int = 100) -> Sequence[OutboxEvent]:
        try:
            async with self._session_factory() as session:
                records = list(
                    await session.scalars(
                        select(BenchmarkOutboxEventRecord)
                        .where(
                            BenchmarkOutboxEventRecord.published_at.is_(None),
                            BenchmarkOutboxEventRecord.next_attempt_at <= now,
                        )
                        .order_by(
                            BenchmarkOutboxEventRecord.created_at,
                            BenchmarkOutboxEventRecord.event_id,
                        )
                        .limit(limit)
                    )
                )
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark outbox persistence is unavailable.") from error
        return [_outbox_from_record(record) for record in records]

    async def mark_outbox_published(self, event_id: UUID, now: datetime) -> bool:
        return await self._update_outbox(event_id, now, published=True, retry_base=0)

    async def mark_outbox_failed(
        self, event_id: UUID, now: datetime, retry_base_delay_seconds: float
    ) -> bool:
        return await self._update_outbox(
            event_id, now, published=False, retry_base=retry_base_delay_seconds
        )

    async def _update_outbox(
        self, event_id: UUID, now: datetime, *, published: bool, retry_base: float
    ) -> bool:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = await session.get(
                        BenchmarkOutboxEventRecord, event_id, with_for_update=True
                    )
                    if record is None or record.published_at is not None:
                        return record is not None and published
                    record.attempt_count += 1
                    if published:
                        record.published_at = now
                        record.last_error_code = None
                    else:
                        record.last_error_code = "queue_unavailable"
                        record.next_attempt_at = now + timedelta(
                            seconds=retry_delay_seconds(record.attempt_count, retry_base)
                        )
            return True
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark outbox persistence is unavailable.") from error

    async def _get_by_idempotency_key(self, key: str) -> BenchmarkRun | None:
        try:
            async with self._session_factory() as session:
                record = await session.scalar(
                    select(BenchmarkRunRecord).where(BenchmarkRunRecord.idempotency_key == key)
                )
        except SQLAlchemyError as error:
            raise PersistenceError("Benchmark idempotency lookup is unavailable.") from error
        return None if record is None else _run_from_record(record)

    @staticmethod
    def _reuse(existing: BenchmarkRun, identity: str) -> tuple[BenchmarkRun, bool]:
        if existing.request_fingerprint != identity:
            raise IdempotencyConflictError(
                "Idempotency-Key was already used for a different benchmark request."
            )
        return existing, False

    @staticmethod
    async def _finish_run_if_terminal(session: AsyncSession, run_id: UUID, now: datetime) -> bool:
        # Serialize terminal reconciliation before counting. Without this run-row lock, concurrent
        # final samples can each observe another transaction's uncommitted nonterminal status and
        # leave a fully terminal run stuck as running.
        run = await session.get(BenchmarkRunRecord, run_id, with_for_update=True)
        if run is None:
            return False
        remaining = await session.scalar(
            select(func.count())
            .select_from(BenchmarkSampleRecord)
            .where(
                BenchmarkSampleRecord.benchmark_run_id == run_id,
                BenchmarkSampleRecord.status.not_in(TERMINAL_SAMPLE_STATUSES),
            )
        )
        if remaining == 0:
            skipped = await session.scalar(
                select(func.count())
                .select_from(BenchmarkSampleRecord)
                .where(
                    BenchmarkSampleRecord.benchmark_run_id == run_id,
                    BenchmarkSampleRecord.status == BenchmarkSampleStatus.SKIPPED,
                )
            )
            run.status = BenchmarkRunStatus.PARTIAL if skipped else BenchmarkRunStatus.COMPLETED
            if run.completed_at is None:
                run.completed_at = now
            return True
        return False


def _run_record(run: BenchmarkRun) -> BenchmarkRunRecord:
    return BenchmarkRunRecord(**run.model_dump(exclude={"model_configs"}))


def _run_from_record(record: BenchmarkRunRecord) -> BenchmarkRun:
    return BenchmarkRun(
        benchmark_run_id=record.benchmark_run_id,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        status=BenchmarkRunStatus(record.status),
        dataset_id=record.dataset_id,
        dataset_version=record.dataset_version,
        dataset_fingerprint=record.dataset_fingerprint,
        benchmark_policy_version=record.benchmark_policy_version,
        coding_prompt_version=record.coding_prompt_version,
        coding_prompt_hash=record.coding_prompt_hash,
        evaluator_fingerprint=record.evaluator_fingerprint,
        benchmark_run_fingerprint=record.benchmark_run_fingerprint,
        samples_per_task=record.samples_per_task,
        planned_sample_count=record.planned_sample_count,
        request_fingerprint=record.request_fingerprint,
        idempotency_key=record.idempotency_key,
    )


def _config_record(config: BenchmarkModelConfig) -> BenchmarkModelConfigRecord:
    pricing = config.pricing
    return BenchmarkModelConfigRecord(
        **config.model_dump(exclude={"pricing"}),
        pricing_version=None if pricing is None else pricing.pricing_version,
        input_cost_per_million_tokens=(
            None if pricing is None else pricing.input_cost_per_million_tokens
        ),
        output_cost_per_million_tokens=(
            None if pricing is None else pricing.output_cost_per_million_tokens
        ),
        currency=None if pricing is None else pricing.currency,
    )


def _config_from_record(record: BenchmarkModelConfigRecord) -> BenchmarkModelConfig:
    pricing = None
    if record.pricing_version is not None:
        if (
            record.input_cost_per_million_tokens is None
            or record.output_cost_per_million_tokens is None
            or record.currency is None
        ):
            raise PersistenceError("Benchmark pricing snapshot is incomplete.")
        pricing = PricingSnapshot(
            pricing_version=record.pricing_version,
            input_cost_per_million_tokens=record.input_cost_per_million_tokens,
            output_cost_per_million_tokens=record.output_cost_per_million_tokens,
            currency=record.currency,
        )
    return BenchmarkModelConfig(
        model_config_id=record.model_config_id,
        benchmark_run_id=record.benchmark_run_id,
        ordinal=record.ordinal,
        provider_id=record.provider_id,
        model=record.model,
        display_name=record.display_name,
        temperature=record.temperature,
        top_p=record.top_p,
        max_output_tokens=record.max_output_tokens,
        seed=record.seed,
        output_mode=GenerationOutputMode(record.output_mode),
        request_timeout_seconds=record.request_timeout_seconds,
        max_concurrent_requests=record.max_concurrent_requests,
        coding_prompt_hash=record.coding_prompt_hash,
        model_configuration_fingerprint=record.model_configuration_fingerprint,
        pricing=pricing,
    )


def _sample_record(sample: BenchmarkSample) -> BenchmarkSampleRecord:
    return BenchmarkSampleRecord(**sample.model_dump())


def _owns_active_benchmark_lease(
    record: BenchmarkSampleRecord, worker_id: str, now: datetime
) -> bool:
    return (
        record.worker_id == worker_id
        and record.lease_expires_at is not None
        and record.lease_expires_at > now
        and BenchmarkSampleStatus(record.status)
        in {BenchmarkSampleStatus.GENERATING, BenchmarkSampleStatus.EVALUATING}
    )


def _sample_from_record(record: BenchmarkSampleRecord) -> BenchmarkSample:
    return BenchmarkSample(
        benchmark_sample_id=record.benchmark_sample_id,
        benchmark_run_id=record.benchmark_run_id,
        model_config_id=record.model_config_id,
        evaluation_id=record.evaluation_id,
        task_id=record.task_id,
        task_version=record.task_version,
        task_fingerprint=record.task_fingerprint,
        tests_fingerprint=record.tests_fingerprint,
        task_weight=record.task_weight,
        sample_index=record.sample_index,
        status=BenchmarkSampleStatus(record.status),
        attempt_count=record.attempt_count,
        max_attempts=record.max_attempts,
        worker_id=record.worker_id,
        lease_expires_at=record.lease_expires_at,
        failure_code=record.failure_code,
        evaluation_duration_seconds=record.evaluation_duration_seconds,
        total_duration_seconds=record.total_duration_seconds,
        created_at=record.created_at,
        updated_at=record.updated_at,
        completed_at=record.completed_at,
    )


def _artifact_record(artifact: GeneratedSolutionArtifact) -> BenchmarkGenerationArtifactRecord:
    return BenchmarkGenerationArtifactRecord(**artifact.model_dump())


def _artifact_from_record(
    record: BenchmarkGenerationArtifactRecord,
) -> GeneratedSolutionArtifact:
    return GeneratedSolutionArtifact(
        benchmark_sample_id=record.benchmark_sample_id,
        source=record.source,
        source_hash=record.source_hash,
        source_size=record.source_size,
        generation_attempts=record.generation_attempts,
        provider_response_id=record.provider_response_id,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        generation_latency_ms=record.generation_latency_ms,
        pricing_version=record.pricing_version,
        generation_cost=record.generation_cost,
        currency=record.currency,
        created_at=record.created_at,
    )


def _outbox_record(
    sample_id: UUID, created_at: datetime, next_attempt_at: datetime | None = None
) -> BenchmarkOutboxEventRecord:
    return BenchmarkOutboxEventRecord(
        event_id=uuid4(),
        aggregate_id=sample_id,
        event_type=BENCHMARK_SAMPLE_REQUESTED,
        created_at=created_at,
        published_at=None,
        attempt_count=0,
        next_attempt_at=next_attempt_at or created_at,
        last_error_code=None,
    )


def _outbox_from_record(record: BenchmarkOutboxEventRecord) -> OutboxEvent:
    return OutboxEvent(
        event_id=record.event_id,
        aggregate_id=record.aggregate_id,
        event_type=record.event_type,
        created_at=record.created_at,
        published_at=record.published_at,
        attempt_count=record.attempt_count,
        next_attempt_at=record.next_attempt_at,
    )
