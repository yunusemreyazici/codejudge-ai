from __future__ import annotations

from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import EvaluationMode, ExecutionBackend, Settings
from app.evaluator.models import RunnerCapability, RunnerResult
from app.main import create_app
from app.queue.redis_streams import QueueMessage
from app.snapshots.models import ExecutionEnvironmentSnapshot
from app.tasks.registry import RegisteredTask
from tests.database.conftest import DatabaseHarness
from tests.database.helpers import snapshot_fixture

pytestmark = pytest.mark.database


class MustNotRunAtSubmission:
    def __init__(self) -> None:
        self.evaluations = 0

    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
        del task, code
        self.evaluations += 1
        raise AssertionError("POST must not run candidate code in async mode")

    async def check_capability(self) -> RunnerCapability:
        return RunnerCapability(backend="test", available=True, detail="available")


class LocalMetadata:
    async def snapshot(self) -> ExecutionEnvironmentSnapshot:
        return ExecutionEnvironmentSnapshot(backend="local")


class FakeQueue:
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
        del message_id

    async def check_capability(self) -> bool:
        return True

    async def heartbeat(self, worker_id: str, ttl_seconds: int) -> None:
        del worker_id, ttl_seconds

    async def active_workers(self) -> int:
        return 2

    async def close(self) -> None: ...


def _settings() -> Settings:
    return Settings(
        log_level="CRITICAL",
        execution_backend=ExecutionBackend.LOCAL,
        persistence_enabled=True,
        database_url="postgresql+asyncpg://unused:unused@localhost/unused",
        evaluation_mode=EvaluationMode.ASYNC,
        redis_url="redis://unused/1",
    )


async def test_async_post_returns_202_without_execution_and_lists_queued_job(
    database_harness: DatabaseHarness,
    correct_lru: str,
) -> None:
    runner = MustNotRunAtSubmission()
    application = create_app(
        settings=_settings(),
        python_runner=runner,
        evaluation_repository=database_harness.repository,
        job_repository=database_harness.job_repository,
        evaluation_queue=FakeQueue(),
        execution_metadata=LocalMetadata(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        posted = await client.post(
            "/api/v1/evaluations",
            json={"task_id": "lru-cache", "language": "python", "code": correct_lru},
        )
        evaluation_id = posted.json()["evaluation_id"]
        detail = await client.get(f"/api/v1/evaluations/{evaluation_id}")
        history = await client.get("/api/v1/evaluations")
        queue_health = await client.get("/health/queue")

    assert posted.status_code == 202
    assert posted.json()["status"] == "queued"
    assert posted.json()["status_url"].endswith(evaluation_id)
    assert detail.json()["status"] == "queued"
    assert detail.json()["attempt_count"] == 0
    assert history.json()[0]["evaluation_id"] == evaluation_id
    assert history.json()[0]["score"] is None
    assert queue_health.json()["active_workers"] == 2
    assert runner.evaluations == 0


async def test_idempotency_header_reuses_same_job_and_conflicts_on_new_payload(
    database_harness: DatabaseHarness,
    correct_lru: str,
) -> None:
    application = create_app(
        settings=_settings(),
        python_runner=MustNotRunAtSubmission(),
        evaluation_repository=database_harness.repository,
        job_repository=database_harness.job_repository,
        evaluation_queue=FakeQueue(),
        execution_metadata=LocalMetadata(),
    )
    request = {"task_id": "lru-cache", "language": "python", "code": correct_lru}
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        first = await client.post(
            "/api/v1/evaluations", json=request, headers={"Idempotency-Key": "stable"}
        )
        replay = await client.post(
            "/api/v1/evaluations", json=request, headers={"Idempotency-Key": "stable"}
        )
        conflict = await client.post(
            "/api/v1/evaluations",
            json={**request, "code": correct_lru + "\n# changed\n"},
            headers={"Idempotency-Key": "stable"},
        )
        no_key = await client.post("/api/v1/evaluations", json=request)

    assert first.status_code == replay.status_code == no_key.status_code == 202
    assert first.json()["evaluation_id"] == replay.json()["evaluation_id"]
    assert no_key.json()["evaluation_id"] != first.json()["evaluation_id"]
    assert conflict.status_code == 409


async def test_async_mode_still_reads_phase4_snapshot_without_job(
    database_harness: DatabaseHarness,
) -> None:
    snapshot = snapshot_fixture()
    await database_harness.repository.create(snapshot)
    application = create_app(
        settings=_settings(),
        python_runner=MustNotRunAtSubmission(),
        evaluation_repository=database_harness.repository,
        job_repository=database_harness.job_repository,
        evaluation_queue=FakeQueue(),
        execution_metadata=LocalMetadata(),
    )

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/v1/evaluations/{snapshot.evaluation_id}")

    assert response.status_code == 200
    assert response.json()["evaluation_id"] == str(snapshot.evaluation_id)
    assert response.json()["score"] == snapshot.final_score
