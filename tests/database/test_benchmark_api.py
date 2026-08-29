from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.service import BenchmarkService
from app.core.config import ExecutionBackend, Settings
from app.evaluator.engine import EvaluationEngine
from app.evaluator.service import EvaluationService
from app.main import create_app
from app.runners.factory import create_python_runner
from app.snapshots.metadata import ExecutionMetadataCollector
from app.tasks.registry import TaskRegistry
from tests.database.conftest import DatabaseHarness
from tests.database.test_benchmark_repository import _plan

pytestmark = pytest.mark.database


async def test_benchmark_api_plans_lists_details_leaderboard_and_compares(
    database_harness: DatabaseHarness,
) -> None:
    settings = Settings(
        log_level="CRITICAL",
        execution_backend=ExecutionBackend.LOCAL,
        static_analysis_enabled=False,
    )
    tasks = TaskRegistry.default()
    evaluations = EvaluationService(
        EvaluationEngine(
            registry=tasks,
            runners={"python": create_python_runner(settings)},
            max_code_size=settings.max_code_size,
            analysis_engine=None,
        ),
        ExecutionMetadataCollector(settings),
        database_harness.repository,
    )
    benchmarks = BenchmarkService(
        database_harness.benchmark_repository,
        BenchmarkDatasetRegistry.default(tasks),
        tasks,
        evaluations,
    )
    application = create_app(
        settings=settings,
        registry=tasks,
        benchmark_service=benchmarks,
    )
    payload = {
        "dataset_id": "codejudge-core",
        "dataset_version": "1",
        "models": [
            {"provider_id": "fake", "model": "good", "temperature": 0},
            {"provider_id": "fake", "model": "bad", "temperature": 0},
            *[
                {"provider_id": "fake", "model": f"catalog-{index}", "temperature": 0}
                for index in range(3, 13)
            ],
        ],
        "samples_per_task": 2,
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/v1/benchmarks", json=payload, headers={"Idempotency-Key": "api-run"}
        )
        duplicate = await client.post(
            "/api/v1/benchmarks", json=payload, headers={"Idempotency-Key": "api-run"}
        )
        run_id = created.json()["benchmark_run_id"]
        fetched = await client.get(f"/api/v1/benchmarks/{run_id}")
        samples = await client.get(f"/api/v1/benchmarks/{run_id}/samples?model=good")
        detail = await client.get(
            f"/api/v1/benchmarks/{run_id}/samples/{samples.json()[0]['benchmark_sample_id']}"
        )
        leaderboard = await client.get(f"/api/v1/benchmarks/{run_id}/leaderboard")
        second = await client.post(
            "/api/v1/benchmarks", json=payload, headers={"Idempotency-Key": "api-run-two"}
        )
        comparison = await client.post(
            "/api/v1/benchmarks/compare",
            json={"run_ids": [run_id, second.json()["benchmark_run_id"]]},
        )
        conflict = await client.post(
            "/api/v1/benchmarks",
            json={**payload, "samples_per_task": 1},
            headers={"Idempotency-Key": "api-run"},
        )
        oversized = await client.post(
            "/api/v1/benchmarks",
            json={**payload, "samples_per_task": 11},
        )
        too_many_models = await client.post(
            "/api/v1/benchmarks",
            json={
                **payload,
                "models": [
                    *payload["models"],
                    *[
                        {
                            "provider_id": "fake",
                            "model": f"catalog-{index}",
                            "temperature": 0,
                        }
                        for index in range(13, 22)
                    ],
                ],
            },
        )

    assert created.status_code == 202
    assert duplicate.status_code == 202
    assert duplicate.json()["benchmark_run_id"] == run_id
    assert fetched.status_code == 200
    assert fetched.json()["planned_samples"] == 24
    assert len(samples.json()) == 2
    assert [sample["sample_index"] for sample in samples.json()] == [1, 2]
    assert len({sample["benchmark_sample_id"] for sample in samples.json()}) == 2
    assert {sample["task_id"] for sample in samples.json()} == {"lru-cache"}
    assert detail.status_code == 200
    assert "source" not in detail.json()
    assert len(leaderboard.json()) == 12
    assert comparison.json()["compatible"] is True
    assert conflict.status_code == 409
    assert oversized.status_code == 422
    assert too_many_models.status_code == 422
    assert "model count exceeds 20" in too_many_models.text

    incompatible_run, config, sample = _plan(idempotency_key=None)
    await database_harness.benchmark_repository.create_plan(incompatible_run, [config], [sample])
    incompatible = await benchmarks.compare([UUID(run_id), incompatible_run.benchmark_run_id])
    assert incompatible.compatible is False
    assert "dataset_fingerprint" in incompatible.differences
    assert "evaluator_fingerprint" in incompatible.differences

    different_samples_run, config, sample = _plan(idempotency_key=None)
    different_samples_run = different_samples_run.model_copy(update={"samples_per_task": 2})
    await database_harness.benchmark_repository.create_plan(
        different_samples_run, [config], [sample]
    )
    different_samples = await benchmarks.compare(
        [incompatible_run.benchmark_run_id, different_samples_run.benchmark_run_id]
    )
    assert "samples_per_task" in different_samples.differences
