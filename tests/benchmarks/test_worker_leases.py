from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.ai.models import ProviderResponse, ProviderUsage, StructuredLLMRequest
from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.models import (
    BenchmarkModelConfig,
    BenchmarkRun,
    BenchmarkRunStatus,
    BenchmarkSample,
    BenchmarkSampleStatus,
    GeneratedSolutionArtifact,
    GenerationOutputMode,
)
from app.benchmarks.worker import BenchmarkWorker
from app.db.repositories import PersistenceError
from app.evaluator.models import EvaluationRequest
from app.snapshots.fingerprints import task_fingerprint
from app.snapshots.fingerprints import tests_fingerprint as _tests_fingerprint
from app.snapshots.models import EvaluationSnapshot
from app.tasks.registry import TaskRegistry
from tests.database.helpers import snapshot_fixture


class ControlledLeaseRepository:
    def __init__(self, outcomes: list[bool | PersistenceError] | None = None) -> None:
        self.outcomes = deque(outcomes or [])
        self.renewal_times: list[float] = []
        self._changed = asyncio.Condition()

    async def renew_lease(self, *args: object) -> bool:
        del args
        async with self._changed:
            self.renewal_times.append(time.monotonic())
            self._changed.notify_all()
        outcome = self.outcomes.popleft() if self.outcomes else True
        if isinstance(outcome, PersistenceError):
            raise outcome
        return outcome

    async def wait_for_renewals(self, count: int) -> None:
        async with self._changed:
            await asyncio.wait_for(
                self._changed.wait_for(lambda: len(self.renewal_times) >= count), timeout=2
            )


class OperationalRepository(ControlledLeaseRepository):
    def __init__(self, run: BenchmarkRun, config: BenchmarkModelConfig) -> None:
        super().__init__()
        self.run = run
        self.config = config
        self.artifact: GeneratedSolutionArtifact | None = None
        self.completions = 0

    async def get_run(self, run_id: object) -> BenchmarkRun | None:
        return self.run if run_id == self.run.benchmark_run_id else None

    async def get_config(self, config_id: object) -> BenchmarkModelConfig | None:
        return self.config if config_id == self.config.model_config_id else None

    async def get_artifact(self, sample_id: object) -> GeneratedSolutionArtifact | None:
        del sample_id
        return self.artifact

    async def store_artifact(
        self,
        sample_id: object,
        worker_id: str,
        artifact: GeneratedSolutionArtifact,
        now: datetime,
    ) -> bool:
        del sample_id, worker_id, now
        self.artifact = artifact
        return True

    async def complete(
        self,
        sample_id: object,
        worker_id: str,
        snapshot: EvaluationSnapshot,
        now: datetime,
        total_duration_seconds: float,
    ) -> bool:
        del sample_id, worker_id, snapshot, now, total_duration_seconds
        self.completions += 1
        return True


class GatedProvider:
    def __init__(self, release: asyncio.Event | None = None) -> None:
        self.release = release
        self.started = asyncio.Event()
        self.calls = 0

    async def complete_raw_source(self, request: StructuredLLMRequest) -> ProviderResponse:
        del request
        self.calls += 1
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        return ProviderResponse(
            content="def candidate():\n    return 1\n",
            response_id="gated-provider",
            usage=ProviderUsage(input_tokens=1, output_tokens=1),
            latency_ms=1,
        )

    async def complete_structured(self, request: StructuredLLMRequest) -> ProviderResponse:
        return await self.complete_raw_source(request)

    async def close(self) -> None: ...


class GatedEvaluations:
    def __init__(self, release: asyncio.Event | None = None) -> None:
        self.release = release
        self.started = asyncio.Event()
        self.calls = 0
        self.task_revisions: list[int | None] = []

    async def get_snapshot(self, evaluation_id: object) -> None:
        del evaluation_id
        return None

    async def evaluate_snapshot(
        self,
        request: EvaluationRequest,
        *,
        evaluation_id: object,
        created_at: datetime,
        task_revision: int | None = None,
    ) -> EvaluationSnapshot:
        del created_at
        self.calls += 1
        self.task_revisions.append(task_revision)
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        return snapshot_fixture(source=request.code, evaluation_id=evaluation_id)


def _sample_and_config(
    *,
    task_id: str = "lru-cache",
    dataset_version: str = "1",
) -> tuple[BenchmarkSample, BenchmarkModelConfig, BenchmarkRun]:
    now = datetime.now(UTC)
    run_id = uuid4()
    config_id = uuid4()
    tasks = TaskRegistry.default()
    datasets = BenchmarkDatasetRegistry.default(tasks)
    dataset = datasets.get("codejudge-core", dataset_version)
    _, task = datasets.resolve_dataset_task(dataset, task_id)
    tests_hash = _tests_fingerprint(task)
    sample = BenchmarkSample(
        benchmark_sample_id=uuid4(),
        benchmark_run_id=run_id,
        model_config_id=config_id,
        evaluation_id=uuid4(),
        task_id=task_id,
        task_version=task.specification.version,
        task_fingerprint=task_fingerprint(task, tests_hash),
        tests_fingerprint=tests_hash,
        task_weight=1,
        sample_index=1,
        status=BenchmarkSampleStatus.GENERATING,
        attempt_count=1,
        max_attempts=3,
        worker_id="lease-test",
        created_at=now,
        updated_at=now,
    )
    config = BenchmarkModelConfig(
        model_config_id=config_id,
        benchmark_run_id=run_id,
        ordinal=0,
        provider_id="fake",
        model="model-a",
        display_name="Model A",
        temperature=0,
        top_p=1,
        max_output_tokens=100,
        output_mode=GenerationOutputMode.RAW_SOURCE,
        coding_prompt_hash="c" * 64,
        model_configuration_fingerprint="d" * 64,
    )
    run = BenchmarkRun(
        benchmark_run_id=run_id,
        created_at=now,
        status=BenchmarkRunStatus.RUNNING,
        dataset_id="codejudge-core",
        dataset_version=dataset_version,
        dataset_fingerprint=dataset.dataset_fingerprint,
        benchmark_policy_version="1",
        coding_prompt_version="2",
        coding_prompt_hash="c" * 64,
        evaluator_fingerprint="f" * 64,
        benchmark_run_fingerprint="1" * 64,
        samples_per_task=1,
        planned_sample_count=1,
        request_fingerprint="2" * 64,
    )
    return sample, config, run


def _sample() -> BenchmarkSample:
    return _sample_and_config()[0]


def _worker(
    repository: ControlledLeaseRepository, *, lease_seconds: float = 0.3
) -> BenchmarkWorker:
    return BenchmarkWorker(
        worker_id="lease-test",
        providers={},
        repository=repository,  # type: ignore[arg-type]
        queue=None,  # type: ignore[arg-type]
        datasets=None,  # type: ignore[arg-type]
        tasks=None,  # type: ignore[arg-type]
        evaluations=None,  # type: ignore[arg-type]
        max_code_size=100_000,
        lease_seconds=lease_seconds,
        retry_base_delay_seconds=0.01,
    )


@pytest.mark.parametrize("blocked_phase", ["provider", "evaluation"])
async def test_long_provider_and_evaluation_paths_renew_before_initial_lease_expiry(
    blocked_phase: str,
) -> None:
    sample, config, run = _sample_and_config()
    release = asyncio.Event()
    provider = GatedProvider(release if blocked_phase == "provider" else None)
    evaluations = GatedEvaluations(release if blocked_phase == "evaluation" else None)
    repository = OperationalRepository(run, config)
    tasks = TaskRegistry.default()
    worker = BenchmarkWorker(
        worker_id="lease-test",
        providers={"fake": provider},
        repository=repository,  # type: ignore[arg-type]
        queue=None,  # type: ignore[arg-type]
        datasets=BenchmarkDatasetRegistry.default(tasks),
        tasks=tasks,
        evaluations=evaluations,  # type: ignore[arg-type]
        max_code_size=100_000,
        lease_seconds=0.3,
        retry_base_delay_seconds=0.01,
    )
    phase_started = provider.started if blocked_phase == "provider" else evaluations.started
    operation_started = time.monotonic()

    with patch(
        "app.benchmarks.worker.benchmark_evaluator_fingerprint",
        new=AsyncMock(return_value=run.evaluator_fingerprint),
    ):
        processing = asyncio.create_task(worker._run_with_lease(sample))
        await asyncio.wait_for(phase_started.wait(), timeout=1)
        await repository.wait_for_renewals(4)
        assert not processing.done()
        assert repository.renewal_times[0] - operation_started < 0.3
        release.set()
        assert await processing is True

    completed_renewals = len(repository.renewal_times)
    await asyncio.sleep(0.12)
    assert len(repository.renewal_times) == completed_renewals
    assert provider.calls == evaluations.calls == repository.completions == 1
    assert evaluations.task_revisions == [1]
    assert repository.artifact is not None


async def test_core_v4_worker_passes_exact_dataset_revision_to_evaluator() -> None:
    sample, config, run = _sample_and_config(
        task_id="frame-decoder",
        dataset_version="4",
    )
    provider = GatedProvider()
    evaluations = GatedEvaluations()
    repository = OperationalRepository(run, config)
    tasks = TaskRegistry.default()
    worker = BenchmarkWorker(
        worker_id="lease-test",
        providers={"fake": provider},
        repository=repository,  # type: ignore[arg-type]
        queue=None,  # type: ignore[arg-type]
        datasets=BenchmarkDatasetRegistry.default(tasks),
        tasks=tasks,
        evaluations=evaluations,  # type: ignore[arg-type]
        max_code_size=100_000,
        lease_seconds=1,
        retry_base_delay_seconds=0.01,
    )

    with patch(
        "app.benchmarks.worker.benchmark_evaluator_fingerprint",
        new=AsyncMock(return_value=run.evaluator_fingerprint),
    ):
        assert await worker._process_claimed(sample) is True

    assert evaluations.task_revisions == [2]
    assert provider.calls == repository.completions == 1


async def test_transient_renewal_failure_recovers_before_ownership_is_lost() -> None:
    repository = ControlledLeaseRepository([PersistenceError("temporary"), True, True])
    worker = _worker(repository, lease_seconds=0.6)
    release = asyncio.Event()

    async def operation(sample: BenchmarkSample) -> bool:
        del sample
        await release.wait()
        return True

    worker._process_claimed = operation  # type: ignore[method-assign]
    processing = asyncio.create_task(worker._run_with_lease(_sample()))

    await repository.wait_for_renewals(2)
    assert not processing.done()
    release.set()
    assert await processing is True


async def test_ownership_loss_cancels_active_operation_and_cannot_report_success() -> None:
    repository = ControlledLeaseRepository([True, False])
    worker = _worker(repository)
    cancelled = asyncio.Event()

    async def operation(sample: BenchmarkSample) -> bool:
        del sample
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    worker._process_claimed = operation  # type: ignore[method-assign]

    assert await worker._run_with_lease(_sample()) is False
    assert cancelled.is_set()
    assert len(repository.renewal_times) == 2


async def test_worker_cancellation_stops_renewal_cleanly() -> None:
    repository = ControlledLeaseRepository()
    worker = _worker(repository)
    cancelled = asyncio.Event()

    async def operation(sample: BenchmarkSample) -> bool:
        del sample
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    worker._process_claimed = operation  # type: ignore[method-assign]
    processing = asyncio.create_task(worker._run_with_lease(_sample()))
    await repository.wait_for_renewals(1)

    processing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await processing
    assert cancelled.is_set()
    completed_renewals = len(repository.renewal_times)
    await asyncio.sleep(0.12)
    assert len(repository.renewal_times) == completed_renewals


async def test_timed_out_operation_stops_renewal_after_failure_transition() -> None:
    repository = ControlledLeaseRepository()
    worker = _worker(repository)

    async def timed_out_operation(sample: BenchmarkSample) -> bool:
        del sample
        await repository.wait_for_renewals(1)
        return True

    worker._process_claimed = timed_out_operation  # type: ignore[method-assign]

    assert await worker._run_with_lease(_sample()) is True
    completed_renewals = len(repository.renewal_times)
    await asyncio.sleep(0.12)
    assert len(repository.renewal_times) == completed_renewals
