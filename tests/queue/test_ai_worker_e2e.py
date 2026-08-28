from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

from app.ai.factory import create_ai_service
from app.ai.models import AIStatus
from app.ai.providers.base import ProviderError
from app.analysis.factory import create_static_analysis_engine
from app.core.config import EvaluationMode, Settings
from app.db.models import EvaluationRecord
from app.evaluator.engine import EvaluationEngine
from app.evaluator.service import EvaluationService
from app.jobs.models import JobStatus
from app.jobs.service import utc_now
from app.main import create_app
from app.queue.outbox import OutboxPublisher
from app.runners.docker_cli import DockerCli
from app.runners.factory import create_python_runner
from app.snapshots.metadata import ExecutionMetadataCollector
from app.tasks.registry import TaskRegistry
from app.worker.service import EvaluationWorker
from tests.ai.fakes import FakeProvider, generated_output, judge_output
from tests.database.conftest import DatabaseHarness
from tests.queue.conftest import RedisHarness

pytestmark = [
    pytest.mark.ai,
    pytest.mark.ai_e2e,
    pytest.mark.queue,
    pytest.mark.sandbox,
    pytest.mark.worker_e2e,
]


def _settings() -> Settings:
    return Settings(
        log_level="CRITICAL",
        persistence_enabled=True,
        database_url=os.environ["CODEJUDGE_TEST_DATABASE_URL"],
        evaluation_mode=EvaluationMode.ASYNC,
        redis_url=os.environ["CODEJUDGE_TEST_REDIS_URL"],
        llm_enabled=True,
        llm_base_url="https://unused.invalid/v1",
        llm_api_key="fake-ci-key",
        llm_provider_id="deterministic-fake",
        llm_judge_models=("judge-a",),
        llm_adversarial_model="generator-a",
    )


async def _run_one(
    *,
    database_harness: DatabaseHarness,
    redis_harness: RedisHarness,
    correct_lru: str,
    provider: FakeProvider,
) -> tuple[dict[str, object], object]:
    settings = _settings()
    runner = create_python_runner(settings)
    capability = await runner.check_capability()
    if not capability.available:
        diagnostic = f"reason={capability.reason or 'unknown'} detail={capability.detail}"
        if os.getenv("CODEJUDGE_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker sandbox is required: {diagnostic}")
        pytest.skip(diagnostic)
    ai_service = create_ai_service(settings, runner, provider=provider)
    metadata = ExecutionMetadataCollector(settings)
    application = create_app(
        settings=settings,
        python_runner=runner,
        evaluation_repository=database_harness.repository,
        job_repository=database_harness.job_repository,
        evaluation_queue=redis_harness.queue,
        execution_metadata=metadata,
        ai_service=ai_service,
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        posted = await client.post(
            "/api/v1/evaluations",
            json={"task_id": "lru-cache", "language": "python", "code": correct_lru},
        )
        assert posted.status_code == 202
        evaluation_id = posted.json()["evaluation_id"]
        publisher = OutboxPublisher(
            database_harness.job_repository,
            redis_harness.queue,
            retry_base_delay_seconds=0.01,
        )
        assert await publisher.dispatch_once() == 1
        message = await redis_harness.queue.consume("phase6-e2e", block_ms=100)
        assert message is not None
        engine = EvaluationEngine(
            registry=TaskRegistry.default(),
            runners={"python": runner},
            max_code_size=settings.max_code_size,
            analysis_engine=create_static_analysis_engine(settings),
        )
        evaluation_service = EvaluationService(
            engine,
            metadata,
            database_harness.repository,
            ai_service,
        )
        worker = EvaluationWorker(
            worker_id="phase6-e2e",
            evaluation_service=evaluation_service,
            job_repository=database_harness.job_repository,
            queue=redis_harness.queue,
            lease_seconds=60,
            retry_base_delay_seconds=0.01,
        )
        await worker.process_message(message)
        response = await client.get(f"/api/v1/evaluations/{evaluation_id}")
    return response.json(), await database_harness.job_repository.get(message.evaluation_id)


async def test_phase6_fake_llm_real_docker_worker_end_to_end(
    database_harness: DatabaseHarness,
    redis_harness: RedisHarness,
    correct_lru: str,
) -> None:
    provider = FakeProvider()
    provider.add("judge", "judge-a", [judge_output(90)])
    provider.add("adversarial", "generator-a", [generated_output()])
    completed, job = await _run_one(
        database_harness=database_harness,
        redis_harness=redis_harness,
        correct_lru=correct_lru,
        provider=provider,
    )
    assessment = completed["ai_assessment"]
    assert isinstance(assessment, dict)
    assert completed["status"] == "completed"
    assert completed["score"] == 100
    assert completed["score_breakdown"]["correctness"] == 100
    assert assessment["status"] == "completed"
    assert assessment["ai_score"] == 93
    assert assessment["provenance"]["reference_fingerprint"]
    assert assessment["adversarial_tests"]["tests"][0]["source"] == ("llm_adversarial_generator")
    assert job is not None and job.status is JobStatus.COMPLETED
    async with database_harness.database.engine.begin() as connection:
        with pytest.raises(DBAPIError, match="evaluation snapshots are immutable"):
            await connection.execute(
                update(EvaluationRecord)
                .where(EvaluationRecord.evaluation_id == job.evaluation_id)
                .values(ai_score=0)
            )
    await _assert_clean(database_harness, redis_harness)


async def test_ai_provider_failure_completes_main_job_without_retry(
    database_harness: DatabaseHarness,
    redis_harness: RedisHarness,
    correct_lru: str,
) -> None:
    provider = FakeProvider()
    provider.add("judge", "judge-a", [ProviderError("provider_timeout")])
    provider.add("adversarial", "generator-a", [ProviderError("provider_unavailable")])
    completed, job = await _run_one(
        database_harness=database_harness,
        redis_harness=redis_harness,
        correct_lru=correct_lru,
        provider=provider,
    )
    assessment = completed["ai_assessment"]
    assert isinstance(assessment, dict)
    assert completed["status"] == "completed"
    assert completed["score"] == 100
    assert assessment["status"] == AIStatus.UNAVAILABLE
    assert assessment.get("ai_score") is None
    assert job is not None and job.status is JobStatus.COMPLETED
    assert job.attempt_count == 1
    await _assert_clean(database_harness, redis_harness)


async def _assert_clean(database_harness: DatabaseHarness, redis_harness: RedisHarness) -> None:
    assert await database_harness.job_repository.ready_outbox(utc_now()) == []
    pending = await redis_harness.raw.xpending(
        redis_harness.queue.stream, redis_harness.queue.group
    )
    assert pending["pending"] == 0
    remaining = await DockerCli().run(
        ["ps", "-a", "--filter", "label=codejudge.component=sandbox", "--format", "{{.Names}}"],
        timeout_seconds=5,
        output_limit_bytes=4096,
    )
    assert remaining.stdout.strip() == ""
