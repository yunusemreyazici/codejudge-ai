"""Idempotent benchmark generation and evaluation worker."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from app.ai.models import StructuredLLMRequest
from app.ai.providers.base import LLMProvider, ProviderError
from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.models import (
    BenchmarkModelConfig,
    BenchmarkSample,
    CodingOutput,
    GeneratedSolutionArtifact,
    GenerationOutputMode,
)
from app.benchmarks.pricing import calculate_generation_cost
from app.benchmarks.prompts import coding_payload, coding_system_prompt
from app.benchmarks.queue import BenchmarkQueueMessage, BenchmarkQueueProtocol
from app.benchmarks.reliability import encode_failure_diagnostic
from app.benchmarks.repositories import BenchmarkRepository
from app.benchmarks.service import benchmark_evaluator_fingerprint
from app.db.repositories import PersistenceError
from app.evaluator.engine import EvaluationInfrastructureError
from app.evaluator.models import EvaluationRequest, Task
from app.evaluator.service import EvaluationService
from app.jobs.service import utc_now
from app.snapshots.fingerprints import source_identity, task_fingerprint, tests_fingerprint
from app.tasks.registry import TaskRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _LeaseHeartbeatState:
    lease_expires_at: datetime
    renewal_count: int
    last_success_monotonic: float


class BenchmarkWorker:
    def __init__(
        self,
        *,
        worker_id: str,
        providers: Mapping[str, LLMProvider],
        repository: BenchmarkRepository,
        queue: BenchmarkQueueProtocol,
        datasets: BenchmarkDatasetRegistry,
        tasks: TaskRegistry,
        evaluations: EvaluationService,
        max_code_size: int,
        lease_seconds: float,
        retry_base_delay_seconds: float,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.worker_id = worker_id
        self._providers = providers
        self._repository = repository
        self._queue = queue
        self._datasets = datasets
        self._tasks = tasks
        self._evaluations = evaluations
        self._max_code_size = max_code_size
        self._lease_seconds = lease_seconds
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._clock = clock

    async def process_message(self, message: BenchmarkQueueMessage) -> None:
        try:
            sample = await self._repository.get_sample(message.benchmark_sample_id)
            if sample is None or sample.status.value in {
                "completed",
                "generation_failed",
                "evaluation_failed",
                "skipped",
            }:
                await self._queue.acknowledge(message.message_id)
                return
            claimed = await self._repository.claim(
                sample.benchmark_sample_id,
                self.worker_id,
                self._clock(),
                self._lease_seconds,
            )
        except PersistenceError:
            return
        if claimed is None:
            await self._queue.acknowledge(message.message_id)
            return

        logger.info(
            "benchmark lease acquired run_id=%s sample_id=%s worker_id=%s attempt=%d "
            "lease_acquired_at=%s lease_expires_at=%s",
            claimed.benchmark_run_id,
            claimed.benchmark_sample_id,
            self.worker_id,
            claimed.attempt_count,
            claimed.updated_at,
            claimed.lease_expires_at,
        )
        transition_recorded = await self._run_with_lease(claimed)
        if transition_recorded:
            await self._queue.acknowledge(message.message_id)

    async def _run_with_lease(self, claimed: BenchmarkSample) -> bool:
        stop_heartbeat = asyncio.Event()
        if claimed.lease_expires_at is None:
            self._log_lease_loss(
                claimed,
                renewal_count=0,
                reason="missing_persisted_expiry",
                lease_expires_at=None,
            )
            return False
        confirmed = await self._renew_before_expiry(
            claimed,
            stop_heartbeat,
            claimed.lease_expires_at,
            renewal_count=0,
            previous_success_monotonic=None,
            scheduled_for_monotonic=time.monotonic(),
        )
        if confirmed is None:
            return False
        heartbeat = asyncio.create_task(
            self._renew_lease(
                claimed,
                stop_heartbeat,
                state=confirmed,
            )
        )
        processing = asyncio.create_task(self._process_claimed(claimed))
        try:
            done, _ = await asyncio.wait(
                {processing, heartbeat}, return_when=asyncio.FIRST_COMPLETED
            )
            if processing in done:
                transition_recorded = processing.result()
            else:
                heartbeat.result()
                processing.cancel()
                await asyncio.gather(processing, return_exceptions=True)
                transition_recorded = False
        finally:
            stop_heartbeat.set()
            if not processing.done():
                processing.cancel()
            if not heartbeat.done():
                heartbeat.cancel()
            await asyncio.gather(processing, heartbeat, return_exceptions=True)
        return transition_recorded

    async def _process_claimed(self, claimed: BenchmarkSample) -> bool:
        started = time.monotonic()
        try:
            run = await self._repository.get_run(claimed.benchmark_run_id)
            config = await self._repository.get_config(claimed.model_config_id)
            if run is None or config is None:
                raise EvaluationInfrastructureError("benchmark_identity_unavailable")
            dataset = self._datasets.get(run.dataset_id, run.dataset_version)
            current_evaluator = await benchmark_evaluator_fingerprint(
                dataset.task_entries, self._tasks, self._evaluations
            )
            if current_evaluator != run.evaluator_fingerprint:
                raise EvaluationInfrastructureError("benchmark_evaluator_identity_mismatch")
            entry, task = self._datasets.resolve_dataset_task(dataset, claimed.task_id)
            current_tests = tests_fingerprint(task)
            if (
                task.specification.version != claimed.task_version
                or current_tests != claimed.tests_fingerprint
                or task_fingerprint(task, current_tests) != claimed.task_fingerprint
            ):
                raise EvaluationInfrastructureError("benchmark_task_identity_mismatch")

            artifact = await self._repository.get_artifact(claimed.benchmark_sample_id)
            if artifact is None:
                artifact = await self._generate(claimed, config, task.specification)
                stored = await self._repository.store_artifact(
                    claimed.benchmark_sample_id,
                    self.worker_id,
                    artifact,
                    self._clock(),
                )
                if not stored:
                    return False
            snapshot = await self._evaluations.get_snapshot(claimed.evaluation_id)
            if snapshot is None:
                snapshot = await self._evaluations.evaluate_snapshot(
                    _candidate_evaluation_request(claimed, artifact.source),
                    evaluation_id=claimed.evaluation_id,
                    created_at=claimed.created_at,
                    task_revision=entry.resolved_task_revision,
                )
            elif (
                snapshot.source_hash != artifact.source_hash
                or snapshot.task_fingerprint != claimed.task_fingerprint
                or snapshot.tests_fingerprint != claimed.tests_fingerprint
            ):
                raise EvaluationInfrastructureError("benchmark_evaluation_identity_mismatch")
            return await self._repository.complete(
                claimed.benchmark_sample_id,
                self.worker_id,
                snapshot,
                self._clock(),
                max(0, time.monotonic() - started),
            )
        except ProviderError as error:
            return (
                await self._repository.record_failure(
                    claimed.benchmark_sample_id,
                    self.worker_id,
                    encode_failure_diagnostic(
                        _generation_failure_code(error.code), error.detail_code
                    ),
                    generation=True,
                    retryable=error.transient,
                    now=self._clock(),
                    retry_base_delay_seconds=self._retry_base_delay_seconds,
                )
                is not None
            )
        except EvaluationInfrastructureError as error:
            return (
                await self._repository.record_failure(
                    claimed.benchmark_sample_id,
                    self.worker_id,
                    str(error),
                    generation=False,
                    retryable=True,
                    now=self._clock(),
                    retry_base_delay_seconds=self._retry_base_delay_seconds,
                )
                is not None
            )
        except PersistenceError:
            return False
        except Exception as error:
            return (
                await self._repository.record_failure(
                    claimed.benchmark_sample_id,
                    self.worker_id,
                    f"worker_{type(error).__name__.lower()}",
                    generation=False,
                    retryable=False,
                    now=self._clock(),
                    retry_base_delay_seconds=self._retry_base_delay_seconds,
                )
                is not None
            )

    async def _generate(
        self, sample: BenchmarkSample, config: BenchmarkModelConfig, task: Task
    ) -> GeneratedSolutionArtifact:
        provider = self._providers.get(config.provider_id)
        if provider is None:
            raise ProviderError("provider_not_configured")
        request = StructuredLLMRequest(
            component="coding_generation",
            model=config.model,
            system_prompt=coding_system_prompt(config.output_mode),
            input_payload=coding_payload(task, config.output_mode),
            response_schema=CodingOutput.model_json_schema(),
            max_output_tokens=config.max_output_tokens,
            temperature=config.temperature,
            top_p=config.top_p,
            seed=config.seed,
        )
        if config.output_mode is GenerationOutputMode.RAW_SOURCE:
            response = await provider.complete_raw_source(request)
            if not response.content.strip():
                raise ProviderError("empty_output", detail_code="empty_output")
            source = response.content
        else:
            response = await provider.complete_structured(request)
            try:
                output = CodingOutput.model_validate(json.loads(response.content))
            except (json.JSONDecodeError, ValidationError) as error:
                raise ProviderError("malformed_output") from error
            source = output.source
        source_hash, source_size = source_identity(source)
        if source_size > self._max_code_size:
            raise ProviderError("output_too_large")
        cost = calculate_generation_cost(
            config.pricing,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        return GeneratedSolutionArtifact(
            benchmark_sample_id=sample.benchmark_sample_id,
            source=source,
            source_hash=source_hash,
            source_size=source_size,
            generation_attempts=sample.attempt_count,
            provider_response_id=response.response_id,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            generation_latency_ms=response.latency_ms,
            pricing_version=(None if config.pricing is None else config.pricing.pricing_version),
            generation_cost=cost,
            currency=None if config.pricing is None else config.pricing.currency,
            created_at=self._clock(),
        )

    async def _renew_lease(
        self,
        sample: BenchmarkSample,
        stop: asyncio.Event,
        *,
        state: _LeaseHeartbeatState,
    ) -> None:
        interval = self._lease_seconds / 3
        while not stop.is_set():
            remaining = (state.lease_expires_at - self._clock()).total_seconds()
            if remaining <= 0:
                self._log_lease_loss(
                    sample,
                    state.renewal_count,
                    "authoritative_deadline_elapsed",
                    lease_expires_at=state.lease_expires_at,
                    remaining_margin_seconds=remaining,
                    seconds_since_success=time.monotonic() - state.last_success_monotonic,
                )
                return
            delay = min(interval, remaining / 2)
            scheduled_for = time.monotonic() + delay
            if await self._wait_for_stop(stop, delay):
                return
            confirmed = await self._renew_before_expiry(
                sample,
                stop,
                state.lease_expires_at,
                state.renewal_count,
                previous_success_monotonic=state.last_success_monotonic,
                scheduled_for_monotonic=scheduled_for,
            )
            if confirmed is None:
                return
            state = confirmed

    async def _renew_before_expiry(
        self,
        sample: BenchmarkSample,
        stop: asyncio.Event,
        lease_expires_at: datetime,
        renewal_count: int,
        *,
        previous_success_monotonic: float | None,
        scheduled_for_monotonic: float,
    ) -> _LeaseHeartbeatState | None:
        retry_interval = self._lease_seconds / 12
        while not stop.is_set():
            request_started_monotonic = time.monotonic()
            remaining = (lease_expires_at - self._clock()).total_seconds()
            if remaining <= 0:
                self._log_lease_loss(
                    sample,
                    renewal_count,
                    "renewal_unconfirmed_before_expiry",
                    lease_expires_at=lease_expires_at,
                    remaining_margin_seconds=remaining,
                    seconds_since_success=(
                        None
                        if previous_success_monotonic is None
                        else request_started_monotonic - previous_success_monotonic
                    ),
                    scheduling_lateness_seconds=max(
                        0.0, request_started_monotonic - scheduled_for_monotonic
                    ),
                )
                return None
            renewal_started_at = self._clock()
            try:
                async with asyncio.timeout(remaining):
                    renewed = await self._repository.renew_lease(
                        sample.benchmark_sample_id,
                        self.worker_id,
                        renewal_started_at,
                        self._lease_seconds,
                    )
            except (PersistenceError, TimeoutError) as error:
                request_finished_monotonic = time.monotonic()
                logger.warning(
                    "benchmark lease renewal deferred run_id=%s sample_id=%s "
                    "worker_id=%s attempt=%d renewal_count=%d error_type=%s "
                    "lease_expires_at=%s renewal_latency_seconds=%.6f "
                    "remaining_margin_seconds=%.6f scheduling_lateness_seconds=%.6f",
                    sample.benchmark_run_id,
                    sample.benchmark_sample_id,
                    self.worker_id,
                    sample.attempt_count,
                    renewal_count,
                    type(error).__name__,
                    lease_expires_at,
                    request_finished_monotonic - request_started_monotonic,
                    (lease_expires_at - self._clock()).total_seconds(),
                    max(0.0, request_started_monotonic - scheduled_for_monotonic),
                )
                remaining = (lease_expires_at - self._clock()).total_seconds()
                if remaining <= 0:
                    self._log_lease_loss(
                        sample,
                        renewal_count,
                        "renewal_unconfirmed_before_expiry",
                        lease_expires_at=lease_expires_at,
                        remaining_margin_seconds=remaining,
                        seconds_since_success=(
                            None
                            if previous_success_monotonic is None
                            else request_finished_monotonic - previous_success_monotonic
                        ),
                        scheduling_lateness_seconds=max(
                            0.0, request_started_monotonic - scheduled_for_monotonic
                        ),
                    )
                    return None
                if await self._wait_for_stop(stop, min(retry_interval, remaining)):
                    return None
                continue
            if not renewed:
                request_finished_monotonic = time.monotonic()
                self._log_lease_loss(
                    sample,
                    renewal_count,
                    "repository_rejected_renewal",
                    lease_expires_at=lease_expires_at,
                    remaining_margin_seconds=(lease_expires_at - self._clock()).total_seconds(),
                    seconds_since_success=(
                        None
                        if previous_success_monotonic is None
                        else request_finished_monotonic - previous_success_monotonic
                    ),
                    renewal_latency_seconds=(
                        request_finished_monotonic - request_started_monotonic
                    ),
                    scheduling_lateness_seconds=max(
                        0.0, request_started_monotonic - scheduled_for_monotonic
                    ),
                )
                return None
            renewal_count += 1
            request_finished_monotonic = time.monotonic()
            persisted_remaining = (renewed.lease_expires_at - self._clock()).total_seconds()
            if persisted_remaining <= 0:
                self._log_lease_loss(
                    sample,
                    renewal_count,
                    "renewal_response_arrived_after_expiry",
                    lease_expires_at=renewed.lease_expires_at,
                    remaining_margin_seconds=persisted_remaining,
                    seconds_since_success=(
                        None
                        if previous_success_monotonic is None
                        else request_finished_monotonic - previous_success_monotonic
                    ),
                    renewal_latency_seconds=(
                        request_finished_monotonic - request_started_monotonic
                    ),
                    scheduling_lateness_seconds=max(
                        0.0, request_started_monotonic - scheduled_for_monotonic
                    ),
                )
                return None
            logger.debug(
                "benchmark lease renewed run_id=%s sample_id=%s worker_id=%s "
                "attempt=%d renewal_count=%d renewed_at=%s lease_expires_at=%s "
                "renewal_latency_seconds=%.6f remaining_margin_seconds=%.6f "
                "seconds_since_success=%s scheduling_lateness_seconds=%.6f",
                sample.benchmark_run_id,
                sample.benchmark_sample_id,
                self.worker_id,
                sample.attempt_count,
                renewal_count,
                renewed.renewed_at,
                renewed.lease_expires_at,
                request_finished_monotonic - request_started_monotonic,
                persisted_remaining,
                (
                    "initial"
                    if previous_success_monotonic is None
                    else f"{request_finished_monotonic - previous_success_monotonic:.6f}"
                ),
                max(0.0, request_started_monotonic - scheduled_for_monotonic),
            )
            return _LeaseHeartbeatState(
                lease_expires_at=renewed.lease_expires_at,
                renewal_count=renewal_count,
                last_success_monotonic=request_finished_monotonic,
            )
        return None

    @staticmethod
    async def _wait_for_stop(stop: asyncio.Event, delay: float) -> bool:
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            return False
        return True

    def _log_lease_loss(
        self,
        sample: BenchmarkSample,
        renewal_count: int,
        reason: str,
        *,
        lease_expires_at: datetime | None,
        remaining_margin_seconds: float | None = None,
        seconds_since_success: float | None = None,
        renewal_latency_seconds: float | None = None,
        scheduling_lateness_seconds: float | None = None,
    ) -> None:
        logger.warning(
            "benchmark lease ownership lost run_id=%s sample_id=%s worker_id=%s "
            "attempt=%d renewal_count=%d reason=%s lease_expires_at=%s "
            "remaining_margin_seconds=%s seconds_since_success=%s "
            "renewal_latency_seconds=%s scheduling_lateness_seconds=%s "
            "ownership_lost=true",
            sample.benchmark_run_id,
            sample.benchmark_sample_id,
            self.worker_id,
            sample.attempt_count,
            renewal_count,
            reason,
            lease_expires_at,
            remaining_margin_seconds,
            seconds_since_success,
            renewal_latency_seconds,
            scheduling_lateness_seconds,
        )


def _generation_failure_code(code: str) -> str:
    mapping = {
        "provider_timeout": "provider_timeout",
        "provider_unavailable": "provider_unavailable",
        "provider_rate_limited": "provider_rate_limited",
        "provider_unauthorized": "provider_unauthorized",
        "provider_forbidden": "provider_forbidden",
        "provider_not_found": "provider_not_found",
        "provider_error": "provider_error",
        "provider_refusal": "provider_refusal",
        "malformed_output": "malformed_output",
        "malformed_provider_response": "malformed_provider_response",
        "provider_output_too_large": "output_too_large",
        "output_too_large": "output_too_large",
        "provider_request_rejected": "provider_request_rejected",
        "provider_not_configured": "provider_not_configured",
        "empty_output": "empty_output",
    }
    return mapping.get(code, "provider_error")


def _candidate_evaluation_request(
    sample: BenchmarkSample,
    source: str,
) -> EvaluationRequest:
    return EvaluationRequest(
        task_id=sample.task_id,
        language="python",
        code=source,
    )
