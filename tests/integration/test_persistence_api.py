from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from app.core.config import ExecutionBackend, Settings
from app.db.repositories import PersistenceError
from app.evaluator.models import RunnerCapability, RunnerResult
from app.main import create_app
from app.runners.python_runner import PythonRunner
from app.snapshots.models import (
    EvaluationSnapshot,
    EvaluationSummary,
    ExecutionEnvironmentSnapshot,
)
from app.tasks.registry import RegisteredTask


class InMemoryEvaluationRepository:
    def __init__(self, *, fail_create: bool = False) -> None:
        self.snapshots: dict[UUID, EvaluationSnapshot] = {}
        self.fail_create = fail_create

    async def create(self, snapshot: EvaluationSnapshot) -> EvaluationSnapshot:
        if self.fail_create:
            raise PersistenceError("raw database detail")
        self.snapshots[snapshot.evaluation_id] = snapshot
        return snapshot

    async def get(self, evaluation_id: UUID) -> EvaluationSnapshot | None:
        return self.snapshots.get(evaluation_id)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        task_id: str | None = None,
        language: str | None = None,
        minimum_score: float | None = None,
        maximum_score: float | None = None,
    ) -> list[EvaluationSummary]:
        snapshots = sorted(
            self.snapshots.values(),
            key=lambda item: (item.created_at, item.evaluation_id),
            reverse=True,
        )
        if task_id is not None:
            snapshots = [item for item in snapshots if item.task_id == task_id]
        if language is not None:
            snapshots = [item for item in snapshots if item.language == language]
        if minimum_score is not None:
            snapshots = [item for item in snapshots if item.final_score >= minimum_score]
        if maximum_score is not None:
            snapshots = [item for item in snapshots if item.final_score <= maximum_score]
        return [
            EvaluationSummary(
                evaluation_id=item.evaluation_id,
                created_at=item.created_at,
                task_id=item.task_id,
                task_version=item.task_version,
                language=item.language,
                source_hash=item.source_hash,
                status=item.status,
                score=item.final_score,
                score_breakdown=item.score_breakdown,
            )
            for item in snapshots[offset : offset + limit]
        ]

    async def check_capability(self) -> bool:
        return True


class CountingRunner:
    def __init__(self) -> None:
        self.delegate = PythonRunner()
        self.evaluations = 0

    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
        self.evaluations += 1
        return await self.delegate.evaluate(task, code)

    async def check_capability(self) -> RunnerCapability:
        return await self.delegate.check_capability()


class LocalMetadata:
    async def snapshot(self) -> ExecutionEnvironmentSnapshot:
        return ExecutionEnvironmentSnapshot(backend="local")


@asynccontextmanager
async def _persistent_client(
    repository: InMemoryEvaluationRepository,
    runner: CountingRunner,
) -> AsyncIterator[AsyncClient]:
    application = create_app(
        settings=Settings(log_level="CRITICAL", execution_backend=ExecutionBackend.LOCAL),
        python_runner=runner,
        evaluation_repository=repository,
        execution_metadata=LocalMetadata(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client


async def test_post_get_and_list_use_immutable_snapshot(
    correct_lru: str,
) -> None:
    repository = InMemoryEvaluationRepository()
    runner = CountingRunner()
    async with _persistent_client(repository, runner) as client:
        posted = await client.post(
            "/api/v1/evaluations",
            json={"task_id": "lru-cache", "language": "python", "code": correct_lru},
        )
        assert posted.status_code == 200
        evaluation_id = posted.json()["evaluation_id"]
        assert posted.json()["created_at"]
        assert runner.evaluations == 1

        detail = await client.get(f"/api/v1/evaluations/{evaluation_id}")
        history = await client.get("/api/v1/evaluations")

    assert detail.status_code == 200
    assert detail.json()["source_text"] == correct_lru
    assert detail.json()["score"] == posted.json()["score"]
    assert detail.json()["task_version"] == "1.0"
    assert history.status_code == 200
    assert history.json()[0]["evaluation_id"] == evaluation_id
    assert "source_text" not in history.json()[0]
    assert "findings" not in history.json()[0]
    assert runner.evaluations == 1


async def test_identical_submissions_receive_distinct_ids(correct_lru: str) -> None:
    repository = InMemoryEvaluationRepository()
    runner = CountingRunner()
    async with _persistent_client(repository, runner) as client:
        request = {"task_id": "lru-cache", "language": "python", "code": correct_lru}
        first = await client.post("/api/v1/evaluations", json=request)
        second = await client.post("/api/v1/evaluations", json=request)

    assert first.json()["evaluation_id"] != second.json()["evaluation_id"]
    first_snapshot, second_snapshot = repository.snapshots.values()
    assert first_snapshot.source_hash == second_snapshot.source_hash
    assert first_snapshot.reproducibility_fingerprint == second_snapshot.reproducibility_fingerprint


async def test_unknown_stored_evaluation_returns_404(correct_lru: str) -> None:
    del correct_lru
    async with _persistent_client(InMemoryEvaluationRepository(), CountingRunner()) as client:
        response = await client.get(f"/api/v1/evaluations/{uuid4()}")

    assert response.status_code == 404


async def test_persistence_failure_does_not_return_fake_success(correct_lru: str) -> None:
    repository = InMemoryEvaluationRepository(fail_create=True)
    async with _persistent_client(repository, CountingRunner()) as client:
        response = await client.post(
            "/api/v1/evaluations",
            json={"task_id": "lru-cache", "language": "python", "code": correct_lru},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Evaluation persistence is unavailable."}
    assert "raw database detail" not in response.text


async def test_disabled_history_is_explicitly_unavailable(client: AsyncClient) -> None:
    history = await client.get("/api/v1/evaluations")
    database_health = await client.get("/health/database")

    assert history.status_code == 503
    assert "persistence is disabled" in history.json()["detail"]
    assert database_health.json() == {
        "configured": False,
        "available": False,
        "detail": "Persistence is disabled.",
    }
