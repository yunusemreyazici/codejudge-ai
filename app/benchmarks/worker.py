"""Idempotent benchmark generation and evaluation worker."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from uuid import UUID

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
from app.benchmarks.repositories import BenchmarkRepository
from app.benchmarks.service import benchmark_evaluator_fingerprint
from app.db.repositories import PersistenceError
from app.evaluator.engine import EvaluationInfrastructureError
from app.evaluator.models import EvaluationRequest, Task
from app.evaluator.service import EvaluationService
from app.jobs.service import utc_now
from app.snapshots.fingerprints import source_identity, task_fingerprint, tests_fingerprint
from app.tasks.registry import TaskRegistry


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

        started = time.monotonic()
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._renew_lease(claimed.benchmark_sample_id, stop_heartbeat)
        )
        transition_recorded = False
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
            task = self._tasks.get(claimed.task_id)
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
                    return
            snapshot = await self._evaluations.get_snapshot(claimed.evaluation_id)
            if snapshot is None:
                snapshot = await self._evaluations.evaluate_snapshot(
                    _candidate_evaluation_request(claimed, artifact.source),
                    evaluation_id=claimed.evaluation_id,
                    created_at=claimed.created_at,
                )
            elif (
                snapshot.source_hash != artifact.source_hash
                or snapshot.task_fingerprint != claimed.task_fingerprint
                or snapshot.tests_fingerprint != claimed.tests_fingerprint
            ):
                raise EvaluationInfrastructureError("benchmark_evaluation_identity_mismatch")
            transition_recorded = await self._repository.complete(
                claimed.benchmark_sample_id,
                self.worker_id,
                snapshot,
                self._clock(),
                max(0, time.monotonic() - started),
            )
        except ProviderError as error:
            transition_recorded = (
                await self._repository.record_failure(
                    claimed.benchmark_sample_id,
                    self.worker_id,
                    _generation_failure_code(error.code),
                    generation=True,
                    retryable=error.transient,
                    now=self._clock(),
                    retry_base_delay_seconds=self._retry_base_delay_seconds,
                )
                is not None
            )
        except EvaluationInfrastructureError as error:
            transition_recorded = (
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
            transition_recorded = False
        except Exception as error:
            transition_recorded = (
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
        finally:
            stop_heartbeat.set()
            await heartbeat
        if transition_recorded:
            await self._queue.acknowledge(message.message_id)

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
            if response.content == "":
                raise ProviderError("empty_output")
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

    async def _renew_lease(self, sample_id: UUID, stop: asyncio.Event) -> None:
        interval = max(0.1, self._lease_seconds / 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                try:
                    renewed = await self._repository.renew_lease(
                        sample_id,
                        self.worker_id,
                        self._clock(),
                        self._lease_seconds,
                    )
                except PersistenceError:
                    continue
                if not renewed:
                    return


def _generation_failure_code(code: str) -> str:
    mapping = {
        "provider_timeout": "provider_timeout",
        "provider_unavailable": "provider_unavailable",
        "provider_rate_limited": "provider_rate_limited",
        "provider_refusal": "provider_refusal",
        "malformed_output": "malformed_output",
        "malformed_provider_response": "malformed_provider_response",
        "provider_output_too_large": "output_too_large",
        "output_too_large": "output_too_large",
        "provider_request_rejected": "provider_request_rejected",
        "provider_not_configured": "provider_not_configured",
        "empty_output": "empty_output",
    }
    return mapping.get(code, "provider_unavailable")


def _candidate_evaluation_request(sample: BenchmarkSample, source: str) -> EvaluationRequest:
    return EvaluationRequest(task_id=sample.task_id, language="python", code=source)
