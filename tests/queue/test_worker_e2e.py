from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from app.analysis.factory import create_static_analysis_engine
from app.core.config import EvaluationMode, Settings
from app.evaluator.engine import EvaluationEngine
from app.evaluator.service import EvaluationService
from app.jobs.service import utc_now
from app.main import create_app
from app.queue.outbox import OutboxPublisher
from app.runners.docker_cli import DockerCli
from app.runners.factory import create_python_runner
from app.snapshots.metadata import ExecutionMetadataCollector
from app.tasks.registry import TaskRegistry
from app.worker.service import EvaluationWorker
from tests.database.conftest import DatabaseHarness
from tests.queue.conftest import RedisHarness

pytestmark = [pytest.mark.queue, pytest.mark.sandbox, pytest.mark.worker_e2e]


async def test_real_postgres_redis_docker_worker_end_to_end(
    database_harness: DatabaseHarness,
    redis_harness: RedisHarness,
    correct_lru: str,
) -> None:
    database_url = os.environ["CODEJUDGE_TEST_DATABASE_URL"]
    redis_url = os.environ["CODEJUDGE_TEST_REDIS_URL"]
    settings = Settings(
        log_level="CRITICAL",
        persistence_enabled=True,
        database_url=database_url,
        evaluation_mode=EvaluationMode.ASYNC,
        redis_url=redis_url,
    )
    runner = create_python_runner(settings)
    capability = await runner.check_capability()
    if not capability.available:
        if os.getenv("CODEJUDGE_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker sandbox is required: {capability.detail}")
        pytest.skip(capability.detail)

    application = create_app(
        settings=settings,
        python_runner=runner,
        evaluation_repository=database_harness.repository,
        job_repository=database_harness.job_repository,
        evaluation_queue=redis_harness.queue,
        execution_metadata=ExecutionMetadataCollector(settings),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        posted = await client.post(
            "/api/v1/evaluations",
            json={"task_id": "lru-cache", "language": "python", "code": correct_lru},
        )
        evaluation_id = posted.json()["evaluation_id"]
        queued = await client.get(f"/api/v1/evaluations/{evaluation_id}")

        publisher = OutboxPublisher(
            database_harness.job_repository,
            redis_harness.queue,
            retry_base_delay_seconds=0.01,
        )
        assert await publisher.dispatch_once() == 1
        message = await redis_harness.queue.consume("e2e-worker", block_ms=100)
        assert message is not None

        worker_engine = EvaluationEngine(
            registry=TaskRegistry.default(),
            runners={"python": runner},
            max_code_size=settings.max_code_size,
            analysis_engine=create_static_analysis_engine(settings),
        )
        worker_service = EvaluationService(
            worker_engine,
            ExecutionMetadataCollector(settings),
            database_harness.repository,
        )
        worker = EvaluationWorker(
            worker_id="e2e-worker",
            evaluation_service=worker_service,
            job_repository=database_harness.job_repository,
            queue=redis_harness.queue,
            lease_seconds=60,
            retry_base_delay_seconds=0.01,
        )
        await worker.process_message(message)
        completed = await client.get(f"/api/v1/evaluations/{evaluation_id}")

    assert posted.status_code == 202
    assert queued.json()["status"] == "queued"
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["score"] == 100
    assert completed.json()["source_text"] == correct_lru
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
