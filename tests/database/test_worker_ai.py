from __future__ import annotations

import pytest

from app.ai.factory import create_ai_service
from app.ai.models import AIStatus
from app.core.config import ExecutionBackend, Settings
from app.evaluator.engine import EvaluationEngine
from app.evaluator.service import EvaluationService
from app.jobs.models import JobStatus
from app.queue.redis_streams import QueueMessage
from app.runners.python_runner import PythonRunner
from app.snapshots.models import ExecutionEnvironmentSnapshot
from app.tasks.registry import TaskRegistry
from app.worker.service import EvaluationWorker
from tests.ai.fakes import FakeProvider, FakeSandbox
from tests.database.conftest import DatabaseHarness
from tests.database.helpers import job_fixture
from tests.database.test_worker_integration import RecordingQueue

pytestmark = [pytest.mark.ai, pytest.mark.database]


class LocalMetadata:
    async def snapshot(self) -> ExecutionEnvironmentSnapshot:
        return ExecutionEnvironmentSnapshot(backend="local")


async def test_queued_ai_identity_mismatch_skips_ai_but_completes_deterministic_job(
    database_harness: DatabaseHarness,
    correct_lru: str,
) -> None:
    settings = Settings(
        execution_backend=ExecutionBackend.LOCAL,
        static_analysis_enabled=False,
        persistence_enabled=True,
        database_url="postgresql+asyncpg://codejudge:codejudge@localhost/codejudge_test",
        llm_enabled=True,
        llm_base_url="https://unused.invalid/v1",
        llm_api_key="fake",
        llm_provider_id="new-provider",
        llm_judge_models=("judge-a",),
        llm_adversarial_model="generator-a",
    )
    runner = PythonRunner()
    provider = FakeProvider()
    ai_service = create_ai_service(
        settings,
        runner,
        provider=provider,
        adversarial_sandbox=FakeSandbox(),
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
        ai_service,
    )
    job = job_fixture(source=correct_lru)
    await database_harness.job_repository.create(job)
    queue = RecordingQueue()
    worker = EvaluationWorker(
        worker_id="worker-ai-mismatch",
        evaluation_service=service,
        job_repository=database_harness.job_repository,
        queue=queue,
        lease_seconds=60,
        retry_base_delay_seconds=0.01,
    )

    await worker.process_message(QueueMessage(message_id="1-0", evaluation_id=job.evaluation_id))

    stored_job = await database_harness.job_repository.get(job.evaluation_id)
    snapshot = await database_harness.repository.get(job.evaluation_id)
    assert stored_job is not None and stored_job.status is JobStatus.COMPLETED
    assert stored_job.attempt_count == 1
    assert snapshot is not None and snapshot.final_score == 100
    assert snapshot.ai_assessment is not None
    assert snapshot.ai_assessment.status is AIStatus.SKIPPED
    assert snapshot.ai_assessment.reason == "ai_identity_mismatch"
    assert provider.requests == []
