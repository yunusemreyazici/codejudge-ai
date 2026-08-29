from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.providers.base import ProviderError
from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.models import (
    BenchmarkCreateRequest,
    BenchmarkModelRequest,
    BenchmarkSampleStatus,
    GeneratedSolutionArtifact,
    PricingSnapshot,
)
from app.benchmarks.pricing import PricingCatalog
from app.benchmarks.queue import BenchmarkQueueMessage
from app.benchmarks.service import BenchmarkService
from app.benchmarks.worker import BenchmarkWorker
from app.core.config import ExecutionBackend, Settings
from app.evaluator.engine import EvaluationEngine
from app.evaluator.service import EvaluationService
from app.jobs.service import utc_now
from app.runners.factory import create_python_runner
from app.snapshots.fingerprints import source_identity
from app.snapshots.metadata import ExecutionMetadataCollector
from app.tasks.registry import TaskRegistry
from tests.ai.fakes import FakeProvider
from tests.conftest import CORRECT_LRU, INCORRECT_LRU
from tests.database.conftest import DatabaseHarness
from tests.database.helpers import snapshot_fixture

pytestmark = pytest.mark.database


class FakeBenchmarkQueue:
    def __init__(self) -> None:
        self.acknowledged: list[str] = []

    async def acknowledge(self, message_id: str) -> None:
        self.acknowledged.append(message_id)


async def test_fake_models_generate_evaluate_fail_and_rank_without_zero_imputation(
    database_harness: DatabaseHarness,
) -> None:
    settings = Settings(
        log_level="CRITICAL",
        persistence_enabled=True,
        database_url="postgresql+asyncpg://unused/test",
        execution_backend=ExecutionBackend.LOCAL,
        static_analysis_enabled=False,
    )
    tasks = TaskRegistry.default()
    runner = create_python_runner(settings)
    evaluations = EvaluationService(
        EvaluationEngine(
            registry=tasks,
            runners={"python": runner},
            max_code_size=settings.max_code_size,
            analysis_engine=None,
        ),
        ExecutionMetadataCollector(settings),
        database_harness.repository,
    )
    pricing = PricingCatalog(
        {
            ("fake", model): PricingSnapshot(
                pricing_version="fake-v1",
                input_cost_per_million_tokens=Decimal("2"),
                output_cost_per_million_tokens=Decimal("8"),
                currency="USD",
            )
            for model in ("good", "bad", "refusal", "malformed", "oversized")
        }
    )
    service = BenchmarkService(
        database_harness.benchmark_repository,
        BenchmarkDatasetRegistry.default(tasks),
        tasks,
        evaluations,
        pricing=pricing,
    )
    accepted = await service.create(
        BenchmarkCreateRequest(
            dataset_id="codejudge-core",
            dataset_version="1",
            models=[
                BenchmarkModelRequest(provider_id="fake", model="good"),
                BenchmarkModelRequest(provider_id="fake", model="bad"),
                BenchmarkModelRequest(provider_id="fake", model="refusal"),
                BenchmarkModelRequest(provider_id="fake", model="malformed"),
                BenchmarkModelRequest(provider_id="fake", model="oversized"),
            ],
            samples_per_task=1,
        ),
        None,
    )
    provider = FakeProvider()
    provider.add("coding_generation", "good", [{"language": "python", "source": CORRECT_LRU}])
    provider.add("coding_generation", "bad", [{"language": "python", "source": INCORRECT_LRU}])
    provider.add(
        "coding_generation",
        "refusal",
        [ProviderError("provider_refusal")],
    )
    provider.add("coding_generation", "malformed", ["```python\nnot structured"])
    provider.add(
        "coding_generation",
        "oversized",
        [{"language": "python", "source": "x" * (settings.max_code_size + 1)}],
    )
    queue = FakeBenchmarkQueue()
    worker = BenchmarkWorker(
        worker_id="benchmark-test",
        providers={"fake": provider},
        repository=database_harness.benchmark_repository,
        queue=queue,
        datasets=BenchmarkDatasetRegistry.default(tasks),
        tasks=tasks,
        evaluations=evaluations,
        max_code_size=settings.max_code_size,
        lease_seconds=10,
        retry_base_delay_seconds=0.01,
    )
    rows = await database_harness.benchmark_repository.result_rows(accepted.benchmark_run_id)
    for index, row in enumerate(rows):
        await worker.process_message(
            BenchmarkQueueMessage(
                message_id=f"message-{index}",
                benchmark_sample_id=row.benchmark_sample_id,
            )
        )

    summary = await service.get(accepted.benchmark_run_id)
    leaderboard = await service.leaderboard(accepted.benchmark_run_id)
    samples = await service.samples(
        accepted.benchmark_run_id,
        limit=10,
        offset=0,
        model=None,
        task_id=None,
        status=None,
    )

    assert summary.status.value == "completed"
    assert summary.completed_samples == 2
    assert summary.generation_failures == 3
    assert [entry.model for entry in leaderboard[:2]] == ["good", "bad"]
    assert {entry.model for entry in leaderboard[2:]} == {
        "refusal",
        "malformed",
        "oversized",
    }
    assert leaderboard[0].weighted_mean_score == 100
    assert leaderboard[0].correctness_pass_rate == 1
    assert leaderboard[0].end_to_end_success_rate == 1
    assert leaderboard[0].perfect_deterministic_score_rate == 1
    assert leaderboard[0].mean_test_execution_seconds is not None
    assert leaderboard[0].mean_evaluation_lifecycle_seconds is not None
    assert leaderboard[0].generation_costs == {"USD": Decimal("0.000600000000")}
    assert leaderboard[1].weighted_mean_score is not None
    assert leaderboard[1].weighted_mean_score < 100
    assert leaderboard[2].coverage == 0
    assert leaderboard[2].deterministic_scores.count == 0
    refusal = next(sample for sample in samples if sample.model == "refusal")
    assert refusal.status is BenchmarkSampleStatus.GENERATION_FAILED
    assert refusal.deterministic_score is None
    assert refusal.failure_code == "provider_refusal"
    malformed = next(sample for sample in samples if sample.model == "malformed")
    oversized = next(sample for sample in samples if sample.model == "oversized")
    assert malformed.failure_code == "malformed_output"
    assert oversized.failure_code == "output_too_large"
    assert len(provider.requests) == 5

    snapshot_recovery = await service.create(
        BenchmarkCreateRequest(
            dataset_id="codejudge-core",
            dataset_version="1",
            models=[BenchmarkModelRequest(provider_id="fake", model="snapshot-recovery")],
        ),
        None,
    )
    snapshot_row = (
        await database_harness.benchmark_repository.result_rows(snapshot_recovery.benchmark_run_id)
    )[0]
    snapshot_time = utc_now()
    await database_harness.benchmark_repository.claim(
        snapshot_row.benchmark_sample_id, "snapshot-crash", snapshot_time, 0.01
    )
    snapshot_source_hash, snapshot_source_size = source_identity(CORRECT_LRU)
    await database_harness.benchmark_repository.store_artifact(
        snapshot_row.benchmark_sample_id,
        "snapshot-crash",
        GeneratedSolutionArtifact(
            benchmark_sample_id=snapshot_row.benchmark_sample_id,
            source=CORRECT_LRU,
            source_hash=snapshot_source_hash,
            source_size=snapshot_source_size,
            generation_attempts=1,
            generation_latency_ms=7,
            created_at=snapshot_time,
        ),
        snapshot_time,
    )
    await database_harness.repository.create(
        snapshot_fixture(source=CORRECT_LRU, evaluation_id=snapshot_row.sample.evaluation_id)
    )
    await database_harness.benchmark_repository.recover_stale(
        snapshot_time + timedelta(seconds=1), 0.01
    )
    snapshot_worker = BenchmarkWorker(
        worker_id="snapshot-replacement",
        providers={"fake": FakeProvider()},
        repository=database_harness.benchmark_repository,
        queue=queue,
        datasets=BenchmarkDatasetRegistry.default(tasks),
        tasks=tasks,
        evaluations=evaluations,
        max_code_size=settings.max_code_size,
        lease_seconds=10,
        retry_base_delay_seconds=0.01,
    )
    with patch.object(
        evaluations,
        "evaluate_snapshot",
        new=AsyncMock(side_effect=AssertionError("evaluation must not rerun")),
    ):
        await snapshot_worker.process_message(
            BenchmarkQueueMessage(
                message_id="snapshot-redelivery",
                benchmark_sample_id=snapshot_row.benchmark_sample_id,
            )
        )
    recovered_snapshot_sample = await database_harness.benchmark_repository.get_sample(
        snapshot_row.benchmark_sample_id
    )
    assert recovered_snapshot_sample is not None
    assert recovered_snapshot_sample.status is BenchmarkSampleStatus.COMPLETED

    flaky_run = await service.create(
        BenchmarkCreateRequest(
            dataset_id="codejudge-core",
            dataset_version="1",
            models=[BenchmarkModelRequest(provider_id="fake", model="flaky")],
        ),
        None,
    )
    flaky_row = (
        await database_harness.benchmark_repository.result_rows(flaky_run.benchmark_run_id)
    )[0]
    flaky_provider = FakeProvider()
    flaky_provider.add(
        "coding_generation",
        "flaky",
        [
            ProviderError("provider_timeout", transient=True),
            {"language": "python", "source": CORRECT_LRU},
        ],
    )
    flaky_worker = BenchmarkWorker(
        worker_id="flaky-worker",
        providers={"fake": flaky_provider},
        repository=database_harness.benchmark_repository,
        queue=queue,
        datasets=BenchmarkDatasetRegistry.default(tasks),
        tasks=tasks,
        evaluations=evaluations,
        max_code_size=settings.max_code_size,
        lease_seconds=10,
        retry_base_delay_seconds=0.01,
    )
    await flaky_worker.process_message(
        BenchmarkQueueMessage(
            message_id="flaky-first", benchmark_sample_id=flaky_row.benchmark_sample_id
        )
    )
    retrying = await database_harness.benchmark_repository.get_sample(flaky_row.benchmark_sample_id)
    assert retrying is not None and retrying.status is BenchmarkSampleStatus.QUEUED
    await flaky_worker.process_message(
        BenchmarkQueueMessage(
            message_id="flaky-second", benchmark_sample_id=flaky_row.benchmark_sample_id
        )
    )
    retried = await database_harness.benchmark_repository.get_sample(flaky_row.benchmark_sample_id)
    assert retried is not None and retried.status is BenchmarkSampleStatus.COMPLETED
    assert len(flaky_provider.requests) == 2

    resumed = await service.create(
        BenchmarkCreateRequest(
            dataset_id="codejudge-core",
            dataset_version="1",
            models=[BenchmarkModelRequest(provider_id="fake", model="resume")],
            samples_per_task=1,
        ),
        None,
    )
    resume_row = (
        await database_harness.benchmark_repository.result_rows(resumed.benchmark_run_id)
    )[0]
    crash_time = utc_now()
    await database_harness.benchmark_repository.claim(
        resume_row.benchmark_sample_id, "crashed", crash_time, 0.01
    )
    source_hash, source_size = source_identity(CORRECT_LRU)
    await database_harness.benchmark_repository.store_artifact(
        resume_row.benchmark_sample_id,
        "crashed",
        GeneratedSolutionArtifact(
            benchmark_sample_id=resume_row.benchmark_sample_id,
            source=CORRECT_LRU,
            source_hash=source_hash,
            source_size=source_size,
            generation_latency_ms=7,
            created_at=crash_time,
        ),
        crash_time,
    )
    await database_harness.benchmark_repository.recover_stale(
        crash_time + timedelta(seconds=1), 0.01
    )
    empty_provider = FakeProvider()
    resumed_worker = BenchmarkWorker(
        worker_id="replacement",
        providers={"fake": empty_provider},
        repository=database_harness.benchmark_repository,
        queue=queue,
        datasets=BenchmarkDatasetRegistry.default(tasks),
        tasks=tasks,
        evaluations=evaluations,
        max_code_size=settings.max_code_size,
        lease_seconds=10,
        retry_base_delay_seconds=0.01,
    )
    await resumed_worker.process_message(
        BenchmarkQueueMessage(
            message_id="artifact-redelivery",
            benchmark_sample_id=resume_row.benchmark_sample_id,
        )
    )
    resumed_sample = await database_harness.benchmark_repository.get_sample(
        resume_row.benchmark_sample_id
    )
    assert resumed_sample is not None
    assert resumed_sample.status is BenchmarkSampleStatus.COMPLETED
    assert empty_provider.requests == []

    for index, row in enumerate(rows):
        await worker.process_message(
            BenchmarkQueueMessage(
                message_id=f"duplicate-{index}",
                benchmark_sample_id=row.benchmark_sample_id,
            )
        )
    assert len(provider.requests) == 5
