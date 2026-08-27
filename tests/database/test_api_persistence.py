import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import ExecutionBackend, Settings
from app.evaluator.models import RunnerCapability, RunnerResult
from app.main import create_app
from app.runners.python_runner import PythonRunner
from app.snapshots.fingerprints import source_identity
from app.snapshots.models import ExecutionEnvironmentSnapshot
from app.tasks.registry import RegisteredTask
from tests.database.conftest import DatabaseHarness

pytestmark = pytest.mark.database


class CountingRunner:
    def __init__(self) -> None:
        self.delegate = PythonRunner()
        self.evaluations = 0

    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
        self.evaluations += 1
        return await self.delegate.evaluate(task, code)

    async def check_capability(self) -> RunnerCapability:
        return await self.delegate.check_capability()


class ExplodingRunner:
    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
        del task, code
        raise AssertionError("Historical GET must not execute candidate code")

    async def check_capability(self) -> RunnerCapability:
        return RunnerCapability(backend="disabled", available=False, detail="Not used")


class LocalMetadata:
    async def snapshot(self) -> ExecutionEnvironmentSnapshot:
        return ExecutionEnvironmentSnapshot(backend="local")


async def test_post_get_and_list_round_trip_through_postgresql(
    database_harness: DatabaseHarness,
    correct_lru: str,
) -> None:
    runner = CountingRunner()
    application = create_app(
        settings=Settings(log_level="CRITICAL", execution_backend=ExecutionBackend.LOCAL),
        python_runner=runner,
        evaluation_repository=database_harness.repository,
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

    restarted_application = create_app(
        settings=Settings(log_level="CRITICAL", execution_backend=ExecutionBackend.LOCAL),
        python_runner=ExplodingRunner(),
        evaluation_repository=database_harness.repository,
        execution_metadata=LocalMetadata(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=restarted_application), base_url="http://test"
    ) as restarted_client:
        detail = await restarted_client.get(f"/api/v1/evaluations/{evaluation_id}")
        history = await restarted_client.get(
            "/api/v1/evaluations?limit=1&offset=0&task_id=lru-cache"
        )
        database_health = await restarted_client.get("/health/database")

    expected_hash, _ = source_identity(correct_lru)
    assert posted.status_code == 200
    assert detail.status_code == 200
    assert detail.json()["source_hash"] == expected_hash
    assert detail.json()["source_text"] == correct_lru
    assert detail.json()["score"] == posted.json()["score"]
    assert history.json()[0]["evaluation_id"] == evaluation_id
    assert database_health.json()["available"] is True
    assert runner.evaluations == 1
