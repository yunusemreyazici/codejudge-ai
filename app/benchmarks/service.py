"""Benchmark planning, durable reads, leaderboards, and compatibility checks."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.identity import (
    benchmark_run_fingerprint,
    evaluator_fingerprint,
    model_configuration_fingerprint,
    request_fingerprint,
)
from app.benchmarks.models import (
    BENCHMARK_POLICY_VERSION,
    CODING_PROMPT_VERSION,
    BenchmarkAccepted,
    BenchmarkComparison,
    BenchmarkCreateRequest,
    BenchmarkModelConfig,
    BenchmarkRun,
    BenchmarkRunStatus,
    BenchmarkRunSummary,
    BenchmarkSample,
    BenchmarkSampleDetail,
    BenchmarkSampleStatus,
    BenchmarkSampleSummary,
    DatasetTaskEntry,
    LeaderboardEntry,
)
from app.benchmarks.pricing import PricingCatalog
from app.benchmarks.prompts import CODING_PROMPT_HASH, model_coding_prompt_hash
from app.benchmarks.repositories import BenchmarkRepository, BenchmarkResultRow
from app.benchmarks.statistics import build_leaderboard
from app.db.repositories import PersistenceError
from app.evaluator.service import EvaluationService
from app.tasks.registry import TaskRegistry


class BenchmarkError(RuntimeError):
    pass


class BenchmarkNotFoundError(BenchmarkError):
    pass


class BenchmarkLimitError(BenchmarkError):
    pass


class BenchmarkService:
    def __init__(
        self,
        repository: BenchmarkRepository,
        datasets: BenchmarkDatasetRegistry,
        tasks: TaskRegistry,
        evaluations: EvaluationService,
        *,
        pricing: PricingCatalog | None = None,
        max_models: int = 5,
        max_tasks: int = 10,
        max_samples_per_task: int = 10,
        max_total_generations: int = 100,
        max_attempts: int = 3,
    ) -> None:
        self._repository = repository
        self._datasets = datasets
        self._tasks = tasks
        self._evaluations = evaluations
        self._pricing = pricing or PricingCatalog()
        self._max_models = max_models
        self._max_tasks = max_tasks
        self._max_samples_per_task = max_samples_per_task
        self._max_total_generations = max_total_generations
        self._max_attempts = max_attempts

    async def create(
        self, request: BenchmarkCreateRequest, idempotency_key: str | None
    ) -> BenchmarkAccepted:
        dataset = self._datasets.get(request.dataset_id, request.dataset_version)
        planned = len(dataset.task_entries) * len(request.models) * request.samples_per_task
        self._validate_limits(request, len(dataset.task_entries), planned)
        now = datetime.now(UTC)
        run_id = uuid4()
        evaluator_hash = await benchmark_evaluator_fingerprint(
            dataset.task_entries, self._tasks, self._evaluations
        )
        configs: list[BenchmarkModelConfig] = []
        config_hashes: list[str] = []
        for ordinal, model in enumerate(request.models):
            model_prompt_hash = model_coding_prompt_hash(model.output_mode)
            identity = model_configuration_fingerprint(model, model_prompt_hash)
            if identity in config_hashes:
                raise BenchmarkLimitError("Duplicate model configurations are not allowed.")
            config_hashes.append(identity)
            configs.append(
                BenchmarkModelConfig(
                    model_config_id=uuid4(),
                    benchmark_run_id=run_id,
                    ordinal=ordinal,
                    provider_id=model.provider_id,
                    model=model.model,
                    display_name=model.display_name or model.model,
                    temperature=model.temperature,
                    top_p=model.top_p,
                    max_output_tokens=model.max_output_tokens,
                    seed=model.seed,
                    output_mode=model.output_mode,
                    request_timeout_seconds=model.request_timeout_seconds,
                    max_concurrent_requests=model.max_concurrent_requests,
                    coding_prompt_hash=model_prompt_hash,
                    model_configuration_fingerprint=identity,
                    pricing=self._pricing.get(model.provider_id, model.model),
                )
            )
        run_hash = benchmark_run_fingerprint(
            dataset_hash=dataset.dataset_fingerprint,
            ordered_model_hashes=config_hashes,
            samples_per_task=request.samples_per_task,
            coding_prompt_version=CODING_PROMPT_VERSION,
            coding_prompt_hash=CODING_PROMPT_HASH,
            evaluator_hash=evaluator_hash,
            policy_version=BENCHMARK_POLICY_VERSION,
        )
        request_hash = request_fingerprint(request.model_dump(mode="json"))
        run = BenchmarkRun(
            benchmark_run_id=run_id,
            created_at=now,
            status=BenchmarkRunStatus.QUEUED,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.dataset_version,
            dataset_fingerprint=dataset.dataset_fingerprint,
            benchmark_policy_version=BENCHMARK_POLICY_VERSION,
            coding_prompt_version=CODING_PROMPT_VERSION,
            coding_prompt_hash=CODING_PROMPT_HASH,
            evaluator_fingerprint=evaluator_hash,
            benchmark_run_fingerprint=run_hash,
            samples_per_task=request.samples_per_task,
            planned_sample_count=planned,
            request_fingerprint=request_hash,
            idempotency_key=idempotency_key,
            model_configs=tuple(configs),
        )
        samples = [
            BenchmarkSample(
                benchmark_sample_id=uuid4(),
                benchmark_run_id=run_id,
                model_config_id=config.model_config_id,
                evaluation_id=uuid4(),
                task_id=entry.task_id,
                task_version=entry.task_version,
                task_fingerprint=entry.task_fingerprint,
                tests_fingerprint=entry.tests_fingerprint,
                task_weight=entry.weight,
                sample_index=sample_index,
                status=BenchmarkSampleStatus.QUEUED,
                attempt_count=0,
                max_attempts=self._max_attempts,
                created_at=now,
                updated_at=now,
            )
            for config in configs
            for entry in dataset.task_entries
            for sample_index in range(1, request.samples_per_task + 1)
        ]
        try:
            stored, _ = await self._repository.create_plan(run, configs, samples)
        except PersistenceError as error:
            raise BenchmarkError("Benchmark persistence is unavailable.") from error
        return BenchmarkAccepted(
            benchmark_run_id=stored.benchmark_run_id,
            status=stored.status,
            planned_samples=stored.planned_sample_count,
            status_url=f"/api/v1/benchmarks/{stored.benchmark_run_id}",
        )

    async def get(self, run_id: UUID) -> BenchmarkRunSummary:
        run = await self._required_run(run_id)
        rows = await self._repository.result_rows(run_id)
        return BenchmarkRunSummary(
            benchmark_run_id=run.benchmark_run_id,
            status=run.status,
            dataset_id=run.dataset_id,
            dataset_version=run.dataset_version,
            dataset_fingerprint=run.dataset_fingerprint,
            benchmark_run_fingerprint=run.benchmark_run_fingerprint,
            benchmark_policy_version=run.benchmark_policy_version,
            coding_prompt_version=run.coding_prompt_version,
            coding_prompt_hash=run.coding_prompt_hash,
            evaluator_fingerprint=run.evaluator_fingerprint,
            samples_per_task=run.samples_per_task,
            planned_samples=run.planned_sample_count,
            completed_samples=sum(
                row.sample.status in {BenchmarkSampleStatus.COMPLETED} for row in rows
            ),
            generation_failures=sum(
                row.sample.status is BenchmarkSampleStatus.GENERATION_FAILED for row in rows
            ),
            evaluation_failures=sum(
                row.sample.status is BenchmarkSampleStatus.EVALUATION_FAILED for row in rows
            ),
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
            models=list(run.model_configs),
        )

    async def samples(
        self,
        run_id: UUID,
        *,
        limit: int,
        offset: int,
        model: str | None,
        task_id: str | None,
        status: BenchmarkSampleStatus | None,
    ) -> list[BenchmarkSampleSummary]:
        await self._required_run(run_id)
        rows = await self._repository.result_rows(
            run_id, limit=limit, offset=offset, model=model, task_id=task_id, status=status
        )
        return [_summary(row) for row in rows]

    async def sample_detail(self, run_id: UUID, sample_id: UUID) -> BenchmarkSampleDetail:
        await self._required_run(run_id)
        rows = await self._repository.result_rows(run_id)
        row = next((item for item in rows if item.sample.benchmark_sample_id == sample_id), None)
        if row is None:
            raise BenchmarkNotFoundError(f"Unknown benchmark sample: {sample_id}")
        summary = _summary(row)
        artifact = row.artifact
        return BenchmarkSampleDetail(
            **summary.model_dump(),
            provider_id=row.config.provider_id,
            model_configuration_fingerprint=row.config.model_configuration_fingerprint,
            source=None if artifact is None else artifact.source,
            source_hash=None if artifact is None else artifact.source_hash,
            source_size=None if artifact is None else artifact.source_size,
            provider_response_id=None if artifact is None else artifact.provider_response_id,
            pricing_version=None if artifact is None else artifact.pricing_version,
            generation_attempts=(
                row.sample.attempt_count if artifact is None else artifact.generation_attempts
            ),
            generation_parameters={
                "temperature": row.config.temperature,
                "top_p": row.config.top_p,
                "max_output_tokens": row.config.max_output_tokens,
                "seed": row.config.seed,
                "output_mode": row.config.output_mode,
                "request_timeout_seconds": row.config.request_timeout_seconds,
                "max_concurrent_requests": row.config.max_concurrent_requests,
            },
            evaluation_duration_seconds=row.sample.evaluation_duration_seconds,
            total_duration_seconds=row.sample.total_duration_seconds,
        )

    async def leaderboard(self, run_id: UUID) -> list[LeaderboardEntry]:
        run = await self._required_run(run_id)
        rows = await self._repository.result_rows(run_id)
        return build_leaderboard(list(run.model_configs), rows)

    async def compare(self, run_ids: list[UUID]) -> BenchmarkComparison:
        runs = [await self._required_run(run_id) for run_id in run_ids]
        differences: list[str] = []
        for field in (
            "dataset_fingerprint",
            "coding_prompt_version",
            "coding_prompt_hash",
            "evaluator_fingerprint",
            "samples_per_task",
            "benchmark_policy_version",
        ):
            if len({getattr(run, field) for run in runs}) > 1:
                differences.append(field)
        compatible = not differences
        return BenchmarkComparison(
            compatible=compatible,
            differences=differences,
            run_fingerprints={run.benchmark_run_id: run.benchmark_run_fingerprint for run in runs},
            leaderboards=(
                {run.benchmark_run_id: await self.leaderboard(run.benchmark_run_id) for run in runs}
                if compatible
                else None
            ),
        )

    async def _required_run(self, run_id: UUID) -> BenchmarkRun:
        try:
            run = await self._repository.get_run(run_id)
        except PersistenceError as error:
            raise BenchmarkError("Benchmark persistence is unavailable.") from error
        if run is None:
            raise BenchmarkNotFoundError(f"Unknown benchmark run: {run_id}")
        return run

    def _validate_limits(
        self, request: BenchmarkCreateRequest, task_count: int, planned: int
    ) -> None:
        if len(request.models) > self._max_models:
            raise BenchmarkLimitError(f"Benchmark model count exceeds {self._max_models}.")
        if task_count > self._max_tasks:
            raise BenchmarkLimitError(f"Benchmark task count exceeds {self._max_tasks}.")
        if request.samples_per_task > self._max_samples_per_task:
            raise BenchmarkLimitError(f"samples_per_task exceeds {self._max_samples_per_task}.")
        if planned > self._max_total_generations:
            raise BenchmarkLimitError(
                f"Planned generation count exceeds {self._max_total_generations}."
            )


def _summary(row: BenchmarkResultRow) -> BenchmarkSampleSummary:
    artifact = row.artifact
    return BenchmarkSampleSummary(
        benchmark_sample_id=row.sample.benchmark_sample_id,
        model_config_id=row.sample.model_config_id,
        model=row.config.model,
        task_id=row.sample.task_id,
        sample_index=row.sample.sample_index,
        status=row.sample.status,
        deterministic_score=row.deterministic_score,
        ai_score=row.ai_score,
        generation_latency_ms=None if artifact is None else artifact.generation_latency_ms,
        input_tokens=None if artifact is None else artifact.input_tokens,
        output_tokens=None if artifact is None else artifact.output_tokens,
        generation_cost=None if artifact is None else artifact.generation_cost,
        currency=None if artifact is None else artifact.currency,
        evaluation_id=(
            row.sample.evaluation_id
            if row.sample.status is BenchmarkSampleStatus.COMPLETED
            else None
        ),
        failure_code=row.sample.failure_code,
    )


async def benchmark_evaluator_fingerprint(
    entries: Sequence[DatasetTaskEntry],
    tasks: TaskRegistry,
    evaluations: EvaluationService,
) -> str:
    runtime, analyzers, scoring_policy, application_version = await evaluations.runtime_identity()
    ai_identities = {
        entry.task_id: evaluations.ai_identity(tasks.get(entry.task_id)).model_dump(mode="json")
        for entry in entries
    }
    return evaluator_fingerprint(
        {
            "codejudge_version": application_version,
            "scoring_policy_version": scoring_policy,
            "analyzer_versions": analyzers,
            "execution": runtime.model_dump(mode="json"),
            "dataset_tasks": [entry.model_dump(mode="json") for entry in entries],
            "ai_identity_by_task": ai_identities,
        }
    )
