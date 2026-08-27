from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import update

from app.core.config import ExecutionBackend, Settings
from app.db.models import EvaluationJobRecord
from app.evaluator.engine import EvaluationEngine
from app.evaluator.models import RunnerCapability, RunnerResult
from app.evaluator.service import EvaluationService
from app.jobs.models import JobStatus
from app.queue.redis_streams import QueueMessage, QueueUnavailableError
from app.runners.python_runner import PythonRunner
from app.snapshots.models import ExecutionEnvironmentSnapshot
from app.tasks.registry import RegisteredTask, TaskRegistry
from app.worker.service import EvaluationWorker
from tests.database.conftest import DatabaseHarness
from tests.database.helpers import job_fixture

pytestmark = pytest.mark.database


class CountingRunner:
    def __init__(self, infrastructure_error: str | None = None) -> None:
        self.delegate = PythonRunner()
        self.infrastructure_error = infrastructure_error
        self.evaluations = 0

    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
        self.evaluations += 1
        if self.infrastructure_error is not None:
            return RunnerResult(
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=0,
                passed=0,
                failed=0,
                total=0,
                infrastructure_error=self.infrastructure_error,
            )
        return await self.delegate.evaluate(task, code)

    async def check_capability(self) -> RunnerCapability:
        return await self.delegate.check_capability()


class LocalMetadata:
    async def snapshot(self) -> ExecutionEnvironmentSnapshot:
        return ExecutionEnvironmentSnapshot(backend="local")


class RecordingQueue:
    def __init__(self, fail_first_ack: bool = False) -> None:
        self.acknowledged: list[str] = []
        self.fail_first_ack = fail_first_ack

    async def ensure_group(self) -> None: ...

    async def enqueue(self, evaluation_id: UUID) -> str:
        return str(evaluation_id)

    async def consume(self, consumer: str, block_ms: int = 1000) -> QueueMessage | None:
        del consumer, block_ms
        return None

    async def reclaim(self, consumer: str, minimum_idle_ms: int) -> QueueMessage | None:
        del consumer, minimum_idle_ms
        return None

    async def acknowledge(self, message_id: str) -> None:
        if self.fail_first_ack:
            self.fail_first_ack = False
            raise QueueUnavailableError("simulated crash boundary")
        self.acknowledged.append(message_id)

    async def check_capability(self) -> bool:
        return True

    async def heartbeat(self, worker_id: str, ttl_seconds: int) -> None:
        del worker_id, ttl_seconds

    async def active_workers(self) -> int:
        return 1

    async def close(self) -> None: ...


def _worker(
    database_harness: DatabaseHarness,
    runner: CountingRunner,
    queue: RecordingQueue,
    *,
    worker_id: str = "worker-test",
) -> EvaluationWorker:
    settings = Settings(
        log_level="CRITICAL",
        execution_backend=ExecutionBackend.LOCAL,
        static_analysis_enabled=False,
    )
    engine = EvaluationEngine(
        registry=TaskRegistry.default(),
        runners={"python": runner},
        max_code_size=settings.max_code_size,
        analysis_engine=None,
    )
    service = EvaluationService(
        engine,
        LocalMetadata(),
        database_harness.repository,
    )
    return EvaluationWorker(
        worker_id=worker_id,
        evaluation_service=service,
        job_repository=database_harness.job_repository,
        queue=queue,
        lease_seconds=60,
        retry_base_delay_seconds=0.01,
    )


async def test_worker_completes_snapshot_and_duplicate_delivery_does_not_rerun(
    database_harness: DatabaseHarness,
    correct_lru: str,
) -> None:
    job = job_fixture(source=correct_lru)
    await database_harness.job_repository.create(job)
    runner = CountingRunner()
    queue = RecordingQueue()
    worker = _worker(database_harness, runner, queue)

    await worker.process_message(QueueMessage(message_id="1-0", evaluation_id=job.evaluation_id))
    await worker.process_message(QueueMessage(message_id="2-0", evaluation_id=job.evaluation_id))

    stored = await database_harness.job_repository.get(job.evaluation_id)
    snapshot = await database_harness.repository.get(job.evaluation_id)
    assert stored is not None and stored.status is JobStatus.COMPLETED
    assert snapshot is not None and snapshot.final_score == 100
    assert runner.evaluations == 1
    assert queue.acknowledged == ["1-0", "2-0"]


async def test_candidate_syntax_error_is_completed_not_retried(
    database_harness: DatabaseHarness,
) -> None:
    job = job_fixture(source="def broken(:\n")
    await database_harness.job_repository.create(job)
    runner = CountingRunner()
    queue = RecordingQueue()

    await _worker(database_harness, runner, queue).process_message(
        QueueMessage(message_id="1-0", evaluation_id=job.evaluation_id)
    )

    stored = await database_harness.job_repository.get(job.evaluation_id)
    snapshot = await database_harness.repository.get(job.evaluation_id)
    assert stored is not None and stored.status is JobStatus.COMPLETED
    assert snapshot is not None and snapshot.final_score == 0
    assert stored.attempt_count == 1


async def test_retryable_failure_waits_then_stops_at_max_attempts(
    database_harness: DatabaseHarness,
    correct_lru: str,
) -> None:
    job = job_fixture(source=correct_lru, max_attempts=2)
    await database_harness.job_repository.create(job)
    runner = CountingRunner("Docker daemon unavailable")
    queue = RecordingQueue()
    worker = _worker(database_harness, runner, queue)

    await worker.process_message(QueueMessage(message_id="1-0", evaluation_id=job.evaluation_id))
    waiting = await database_harness.job_repository.get(job.evaluation_id)
    assert waiting is not None and waiting.status is JobStatus.RETRY_WAIT
    retry_event = (await database_harness.job_repository.ready_outbox(waiting.next_attempt_at))[-1]
    await database_harness.job_repository.mark_outbox_published(
        retry_event.event_id, waiting.next_attempt_at
    )
    await worker.process_message(QueueMessage(message_id="2-0", evaluation_id=job.evaluation_id))

    failed = await database_harness.job_repository.get(job.evaluation_id)
    assert failed is not None and failed.status is JobStatus.FAILED
    assert failed.attempt_count == 2
    assert failed.last_error_code == "sandbox_unavailable"
    assert await database_harness.repository.get(job.evaluation_id) is None


@pytest.mark.parametrize(
    ("values", "expected_code"),
    [
        ({"source_hash": "0" * 64}, "source_identity_mismatch"),
        ({"task_fingerprint": "0" * 64}, "task_fingerprint_mismatch"),
        ({"tests_fingerprint": "0" * 64}, "tests_fingerprint_mismatch"),
        ({"expected_scoring_policy_version": "old"}, "scoring_policy_version_mismatch"),
        ({"expected_analyzer_versions": {"ruff": "old"}}, "analyzer_versions_mismatch"),
    ],
)
async def test_worker_rejects_changed_persisted_identity(
    database_harness: DatabaseHarness,
    correct_lru: str,
    values: dict[str, object],
    expected_code: str,
) -> None:
    job = job_fixture(source=correct_lru)
    await database_harness.job_repository.create(job)
    async with database_harness.database.engine.begin() as connection:
        await connection.execute(
            update(EvaluationJobRecord)
            .where(EvaluationJobRecord.evaluation_id == job.evaluation_id)
            .values(**values)
        )

    await _worker(database_harness, CountingRunner(), RecordingQueue()).process_message(
        QueueMessage(message_id="1-0", evaluation_id=job.evaluation_id)
    )

    failed = await database_harness.job_repository.get(job.evaluation_id)
    assert failed is not None and failed.status is JobStatus.FAILED
    assert failed.last_error_category == "integrity"
    assert failed.last_error_code == expected_code


async def test_crash_after_snapshot_before_ack_redelivery_is_idempotent(
    database_harness: DatabaseHarness,
    correct_lru: str,
) -> None:
    job = job_fixture(source=correct_lru)
    await database_harness.job_repository.create(job)
    runner = CountingRunner()
    queue = RecordingQueue(fail_first_ack=True)
    worker = _worker(database_harness, runner, queue)

    with pytest.raises(QueueUnavailableError):
        await worker.process_message(
            QueueMessage(message_id="1-0", evaluation_id=job.evaluation_id)
        )
    await worker.process_message(QueueMessage(message_id="1-0", evaluation_id=job.evaluation_id))

    stored = await database_harness.job_repository.get(job.evaluation_id)
    assert stored is not None and stored.status is JobStatus.COMPLETED
    assert runner.evaluations == 1
    assert queue.acknowledged == ["1-0"]
