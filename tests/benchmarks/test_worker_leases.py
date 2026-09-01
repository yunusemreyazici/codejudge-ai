from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import cast
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
from app.benchmarks.repositories import BenchmarkLeaseRenewal
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
        self.renewal_instants: list[datetime] = []
        self._changed = asyncio.Condition()

    async def renew_lease(self, *args: object) -> BenchmarkLeaseRenewal | None:
        async with self._changed:
            self.renewal_times.append(time.monotonic())
            self._changed.notify_all()
        outcome = self.outcomes.popleft() if self.outcomes else True
        if isinstance(outcome, PersistenceError):
            raise outcome
        if not outcome:
            return None
        renewed_at = cast(datetime, args[2])
        self.renewal_instants.append(renewed_at)
        lease_seconds = cast(float, args[3])
        return BenchmarkLeaseRenewal(
            renewed_at=renewed_at,
            lease_expires_at=renewed_at + timedelta(seconds=lease_seconds),
        )

    async def wait_for_renewals(self, count: int) -> None:
        async with self._changed:
            await asyncio.wait_for(
                self._changed.wait_for(lambda: len(self.renewal_times) >= count), timeout=2
            )


class ExpiringLeaseRepository(ControlledLeaseRepository):
    def __init__(self, lease_seconds: float, *, first_delay: float = 0) -> None:
        super().__init__()
        self.lease_seconds = lease_seconds
        self.first_delay = first_delay
        self.expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        self.expiry_history: list[datetime] = []
        self.expired = False

    async def renew_lease(self, *args: object) -> BenchmarkLeaseRenewal | None:
        invocation = cast(datetime, args[2])
        if not self.renewal_times and self.first_delay:
            await asyncio.sleep(self.first_delay)
        async with self._changed:
            self.renewal_times.append(time.monotonic())
            self.renewal_instants.append(invocation)
            self._changed.notify_all()
        if invocation >= self.expires_at:
            return None
        self.expires_at = invocation + timedelta(seconds=self.lease_seconds)
        self.expiry_history.append(self.expires_at)
        return BenchmarkLeaseRenewal(
            renewed_at=invocation,
            lease_expires_at=self.expires_at,
        )

    async def watch_for_expiry(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if datetime.now(UTC) >= self.expires_at:
                self.expired = True
                return
            await asyncio.sleep(self.lease_seconds / 30)


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class ShortPersistedLeaseRepository(ControlledLeaseRepository):
    async def renew_lease(self, *args: object) -> BenchmarkLeaseRenewal:
        renewed_at = cast(datetime, args[2])
        async with self._changed:
            self.renewal_times.append(time.monotonic())
            self.renewal_instants.append(renewed_at)
            self._changed.notify_all()
        return BenchmarkLeaseRenewal(
            renewed_at=renewed_at,
            lease_expires_at=renewed_at + timedelta(seconds=0.05),
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
        lease_expires_at=now + timedelta(seconds=60),
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


def _sample(*, lease_seconds: float = 0.3) -> BenchmarkSample:
    sample = _sample_and_config()[0]
    return sample.model_copy(
        update={"lease_expires_at": sample.updated_at + timedelta(seconds=lease_seconds)}
    )


def _worker(
    repository: ControlledLeaseRepository,
    *,
    lease_seconds: float = 0.3,
    clock: MutableClock | None = None,
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
        clock=clock or (lambda: datetime.now(UTC)),
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


async def test_unconfirmed_renewal_retries_are_bounded_by_original_expiry() -> None:
    lease_seconds = 0.12
    repository = ControlledLeaseRepository([PersistenceError("unavailable") for _ in range(100)])
    worker = _worker(repository, lease_seconds=lease_seconds)
    operation_started = False

    async def operation(sample: BenchmarkSample) -> bool:
        del sample
        nonlocal operation_started
        operation_started = True
        return True

    worker._process_claimed = operation  # type: ignore[method-assign]
    started = time.monotonic()

    assert await worker._run_with_lease(_sample(lease_seconds=lease_seconds)) is False
    assert time.monotonic() - started < lease_seconds * 2
    assert operation_started is False
    assert 2 <= len(repository.renewal_times) <= 14


async def test_long_healthy_processing_never_crosses_the_durable_lease_expiry() -> None:
    lease_seconds = 0.18
    repository = ExpiringLeaseRepository(lease_seconds)
    worker = _worker(repository, lease_seconds=lease_seconds)
    stop_watcher = asyncio.Event()

    async def operation(sample: BenchmarkSample) -> bool:
        del sample
        await asyncio.sleep(lease_seconds * 3)
        return True

    worker._process_claimed = operation  # type: ignore[method-assign]
    watcher = asyncio.create_task(repository.watch_for_expiry(stop_watcher))
    try:
        assert await worker._run_with_lease(_sample(lease_seconds=lease_seconds)) is True
    finally:
        stop_watcher.set()
        await watcher

    assert repository.expired is False
    assert len(repository.renewal_times) >= 4


async def test_slow_initial_renewal_uses_persisted_expiry_not_response_time() -> None:
    lease_seconds = 0.3
    repository = ExpiringLeaseRepository(lease_seconds, first_delay=0.24)
    worker = _worker(repository, lease_seconds=lease_seconds)
    release = asyncio.Event()

    async def operation(sample: BenchmarkSample) -> bool:
        del sample
        await release.wait()
        return True

    worker._process_claimed = operation  # type: ignore[method-assign]
    processing = asyncio.create_task(worker._run_with_lease(_sample()))
    await repository.wait_for_renewals(2)

    assert repository.renewal_instants[1] < repository.expiry_history[0]
    release.set()
    assert await processing is True


async def test_worker_uses_exact_persisted_expiry_returned_by_repository() -> None:
    now = datetime.now(UTC)
    clock = MutableClock(now)
    repository = ShortPersistedLeaseRepository()
    worker = _worker(repository, lease_seconds=0.3, clock=clock)
    sample = _sample().model_copy(update={"lease_expires_at": now + timedelta(seconds=0.3)})

    state = await worker._renew_before_expiry(
        sample,
        asyncio.Event(),
        cast(datetime, sample.lease_expires_at),
        renewal_count=0,
        previous_success_monotonic=None,
        scheduled_for_monotonic=time.monotonic(),
    )

    assert state is not None
    assert state.lease_expires_at == now + timedelta(seconds=0.05)
    assert state.lease_expires_at != now + timedelta(seconds=worker._lease_seconds)


async def test_utc_clock_advance_cannot_leave_worker_believing_lease_is_live(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime.now(UTC)
    clock = MutableClock(now)
    repository = ControlledLeaseRepository()
    worker = _worker(repository, lease_seconds=0.3, clock=clock)
    sample = _sample().model_copy(update={"lease_expires_at": now + timedelta(seconds=0.3)})
    stop = asyncio.Event()
    state = await worker._renew_before_expiry(
        sample,
        stop,
        cast(datetime, sample.lease_expires_at),
        renewal_count=0,
        previous_success_monotonic=None,
        scheduled_for_monotonic=time.monotonic(),
    )
    assert state is not None
    assert len(repository.renewal_times) == 1

    clock.advance(0.31)
    with caplog.at_level("WARNING", logger="app.benchmarks.worker"):
        lost = await worker._renew_before_expiry(
            sample,
            stop,
            state.lease_expires_at,
            renewal_count=state.renewal_count,
            previous_success_monotonic=state.last_success_monotonic,
            scheduled_for_monotonic=time.monotonic(),
        )

    assert lost is None
    assert len(repository.renewal_times) == 1
    assert "reason=renewal_unconfirmed_before_expiry" in caplog.text
    assert "lease_expires_at=" in caplog.text
    assert "remaining_margin_seconds=" in caplog.text
    assert "scheduling_lateness_seconds=" in caplog.text


async def test_utc_clock_advance_cancels_active_processing_after_prior_renewals() -> None:
    lease_seconds = 0.3
    now = datetime.now(UTC)
    clock = MutableClock(now)
    repository = ControlledLeaseRepository()
    worker = _worker(repository, lease_seconds=lease_seconds, clock=clock)
    sample = _sample().model_copy(
        update={"lease_expires_at": now + timedelta(seconds=lease_seconds)}
    )
    cancelled = asyncio.Event()

    async def operation(claimed: BenchmarkSample) -> bool:
        del claimed
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    worker._process_claimed = operation  # type: ignore[method-assign]
    processing = asyncio.create_task(worker._run_with_lease(sample))
    await repository.wait_for_renewals(3)
    confirmed_renewals = len(repository.renewal_times)

    clock.advance(lease_seconds + 0.01)

    assert await asyncio.wait_for(processing, timeout=lease_seconds) is False
    assert cancelled.is_set()
    assert len(repository.renewal_times) == confirmed_renewals


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


async def test_processing_error_stops_renewal_without_leaking_tasks() -> None:
    repository = ControlledLeaseRepository()
    worker = _worker(repository)

    async def operation(sample: BenchmarkSample) -> bool:
        del sample
        await repository.wait_for_renewals(1)
        raise RuntimeError("controlled failure")

    worker._process_claimed = operation  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="controlled failure"):
        await worker._run_with_lease(_sample())

    completed_renewals = len(repository.renewal_times)
    await asyncio.sleep(0.12)
    assert len(repository.renewal_times) == completed_renewals


@pytest.mark.parametrize("terminal_state", ["completed", "generation_failed", "skipped"])
async def test_terminal_transition_stops_renewal(terminal_state: str) -> None:
    repository = ControlledLeaseRepository()
    worker = _worker(repository)
    transitions: list[str] = []

    async def terminal_operation(sample: BenchmarkSample) -> bool:
        del sample
        await repository.wait_for_renewals(1)
        transitions.append(terminal_state)
        return True

    worker._process_claimed = terminal_operation  # type: ignore[method-assign]

    assert await worker._run_with_lease(_sample()) is True
    assert transitions == [terminal_state]
    completed_renewals = len(repository.renewal_times)
    await asyncio.sleep(0.12)
    assert len(repository.renewal_times) == completed_renewals
