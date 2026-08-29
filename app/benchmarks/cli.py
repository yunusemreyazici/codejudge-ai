"""Single Phase 7.2 CLI for planning, running, inspecting, and exporting benchmarks."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from redis.asyncio import Redis

from app.ai.factory import create_ai_service
from app.analysis.factory import create_static_analysis_engine
from app.benchmarks.datasets import BenchmarkDatasetRegistry, DatasetRegistryError
from app.benchmarks.exporting import (
    BenchmarkArtifacts,
    BenchmarkExporter,
    BenchmarkExportError,
    render_report,
    write_export,
)
from app.benchmarks.models import BenchmarkCreateRequest, BenchmarkRunStatus, BenchmarkSampleStatus
from app.benchmarks.repositories import SqlAlchemyBenchmarkRepository
from app.benchmarks.run_config import (
    BenchmarkPlan,
    BenchmarkRunConfig,
    build_plan,
    load_benchmark_config,
    validate_run_preflight,
)
from app.benchmarks.service import BenchmarkError, BenchmarkService
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.repositories import PersistenceError, SqlAlchemyEvaluationRepository
from app.db.session import Database
from app.evaluator.engine import EvaluationEngine
from app.evaluator.service import EvaluationService
from app.runners.factory import create_python_runner
from app.snapshots.metadata import ExecutionMetadataCollector
from app.tasks.registry import TaskRegistry


class BenchmarkCLIError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codejudge-benchmark",
        description="Plan and publish auditable CodeJudge benchmark runs.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="validate a config and estimate its maximum cost")
    plan.add_argument("config", type=Path)
    run = commands.add_parser("run", help="durably accept an explicitly requested benchmark")
    run.add_argument("config", type=Path)
    run.add_argument("--wait", action="store_true", help="wait for a terminal run state")
    status = commands.add_parser("status", help="show durable benchmark progress")
    status.add_argument("run_id", type=UUID)
    export = commands.add_parser("export", help="write deterministic machine-readable results")
    export.add_argument("run_id", type=UUID)
    export.add_argument("--format", choices=("json",), default="json")
    export.add_argument("--output", type=Path)
    export.add_argument("--allow-incomplete", action="store_true")
    report = commands.add_parser("report", help="write a self-contained Markdown report")
    report.add_argument("run_id", type=UUID)
    report.add_argument("--output", type=Path)
    report.add_argument("--allow-incomplete", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        exit_code = asyncio.run(_dispatch(arguments))
    except (
        ValueError,
        BenchmarkCLIError,
        BenchmarkExportError,
        BenchmarkError,
        DatasetRegistryError,
        PersistenceError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130) from None
    raise SystemExit(exit_code)


async def _dispatch(arguments: argparse.Namespace) -> int:
    if arguments.command == "plan":
        config = load_benchmark_config(arguments.config)
        _print_plan(build_plan(config))
        return 0
    if arguments.command == "run":
        return await _run(arguments.config, wait=arguments.wait)
    if arguments.command == "status":
        await _status(arguments.run_id)
        return 0
    if arguments.command == "export":
        output = arguments.output or _generated_directory(arguments.run_id) / "results.json"
        artifacts = await _export(arguments.run_id, arguments.allow_incomplete)
        write_export(artifacts, output)
        print(f"Results: {output}")
        print(f"SHA-256: {artifacts.results_sha256}")
        return 0
    if arguments.command == "report":
        output = arguments.output or _generated_directory(arguments.run_id) / "report.md"
        artifacts = await _export(arguments.run_id, arguments.allow_incomplete)
        results_output = output.parent / "results.json"
        write_export(artifacts, results_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_report(artifacts), encoding="utf-8")
        print(f"Report: {output}")
        print(f"Results: {results_output}")
        print(f"Results SHA-256: {artifacts.results_sha256}")
        return 0
    raise BenchmarkCLIError("Unknown command.")


async def _run(config_path: Path, *, wait: bool) -> int:
    config = load_benchmark_config(config_path)
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    tasks = TaskRegistry.default(settings.default_execution_timeout)
    datasets = BenchmarkDatasetRegistry.default(tasks)
    plan = build_plan(
        config,
        tasks=tasks,
        datasets=datasets,
        max_models=settings.max_benchmark_models,
        max_tasks=settings.max_benchmark_tasks,
        max_samples_per_task=settings.max_benchmark_samples_per_task,
        max_total_generations=settings.max_benchmark_total_generations,
    )
    validate_run_preflight(config, plan, ai_enabled=settings.llm_enabled)
    _required_services(settings)
    await _check_redis(settings.redis_url)
    _print_plan(plan)
    print("\nRun command accepted as the explicit provider-execution boundary.")
    async with _service_runtime(settings, config, tasks, datasets) as (service, repository):
        accepted = await service.create(
            BenchmarkCreateRequest(
                dataset_id=config.dataset.id,
                dataset_version=config.dataset.version,
                models=list(config.models),
                samples_per_task=config.samples_per_task,
            ),
            idempotency_key=None,
        )
        print("Benchmark accepted")
        print(f"Run ID: {accepted.benchmark_run_id}")
        print(f"Planned samples: {accepted.planned_samples}")
        print(f"Dataset fingerprint: {plan.dataset_fingerprint}")
        if wait:
            await _wait_for_terminal(repository, accepted.benchmark_run_id)
        return 0


async def _status(run_id: UUID) -> None:
    settings = Settings.from_env()
    database_url = _required_database(settings)
    database = Database(database_url)
    try:
        repository = SqlAlchemyBenchmarkRepository(database.session_factory)
        run = await repository.get_run(run_id)
        if run is None:
            raise BenchmarkCLIError(f"Unknown benchmark run: {run_id}")
        rows = await repository.result_rows(run_id)
        completed = sum(row.sample.status is BenchmarkSampleStatus.COMPLETED for row in rows)
        generation_failures = sum(row.sample.status.value == "generation_failed" for row in rows)
        evaluation_failures = sum(row.sample.status.value == "evaluation_failed" for row in rows)
        costs: dict[str, Decimal] = {}
        cost_coverage = 0
        for row in rows:
            if (
                row.artifact is not None
                and row.artifact.generation_cost is not None
                and row.artifact.currency
            ):
                costs[row.artifact.currency] = (
                    costs.get(row.artifact.currency, Decimal()) + row.artifact.generation_cost
                )
                cost_coverage += 1
        elapsed_end = run.completed_at or datetime.now(UTC)
        elapsed = max(0.0, (elapsed_end - run.created_at).total_seconds())
        print(f"Run ID: {run_id}")
        print(f"Status: {run.status.value}")
        print(f"Planned: {run.planned_sample_count}")
        print(f"Completed: {completed}")
        print(f"Generation failures: {generation_failures}")
        print(f"Evaluation failures: {evaluation_failures}")
        print(f"Coverage: {completed / run.planned_sample_count:.1%}")
        print(f"Elapsed: {elapsed:.3f} seconds")
        if not costs or cost_coverage < len(rows):
            rendered = "unknown" if not costs else _render_costs(costs) + " (partial)"
        else:
            rendered = _render_costs(costs)
        print(f"Current generation cost: {rendered}")
    finally:
        await database.dispose()


async def _export(run_id: UUID, allow_incomplete: bool) -> BenchmarkArtifacts:
    settings = Settings.from_env()
    database_url = _required_database(settings)
    database = Database(database_url)
    tasks = TaskRegistry.default(settings.default_execution_timeout)
    try:
        exporter = BenchmarkExporter(
            SqlAlchemyBenchmarkRepository(database.session_factory),
            SqlAlchemyEvaluationRepository(database.session_factory),
            BenchmarkDatasetRegistry.default(tasks),
        )
        return await exporter.build(run_id, allow_incomplete=allow_incomplete)
    finally:
        await database.dispose()


@asynccontextmanager
async def _service_runtime(
    settings: Settings,
    config: BenchmarkRunConfig,
    tasks: TaskRegistry,
    datasets: BenchmarkDatasetRegistry,
) -> AsyncIterator[tuple[BenchmarkService, SqlAlchemyBenchmarkRepository]]:
    database_url = _required_database(settings)
    database = Database(database_url)
    repository = SqlAlchemyBenchmarkRepository(database.session_factory)
    evaluations = SqlAlchemyEvaluationRepository(database.session_factory)
    runner = create_python_runner(settings)
    ai_service = create_ai_service(settings, runner)
    evaluation_service = EvaluationService(
        EvaluationEngine(
            registry=tasks,
            runners={"python": runner},
            max_code_size=settings.max_code_size,
            analysis_engine=(
                create_static_analysis_engine(settings)
                if settings.static_analysis_enabled
                else None
            ),
        ),
        ExecutionMetadataCollector(settings),
        evaluations,
        ai_service,
    )
    service = BenchmarkService(
        repository,
        datasets,
        tasks,
        evaluation_service,
        pricing=config.pricing_catalog(),
        max_models=settings.max_benchmark_models,
        max_tasks=settings.max_benchmark_tasks,
        max_samples_per_task=settings.max_benchmark_samples_per_task,
        max_total_generations=settings.max_benchmark_total_generations,
        max_attempts=settings.worker_max_attempts,
    )
    try:
        yield service, repository
    finally:
        await ai_service.close()
        await database.dispose()


async def _check_redis(redis_url: str | None) -> None:
    if redis_url is None:
        raise BenchmarkCLIError("REDIS_URL is required for durable benchmark execution.")
    client: Redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        ping_result = client.ping()
        available = ping_result if isinstance(ping_result, bool) else await ping_result
        if not available:
            raise BenchmarkCLIError("Redis is unavailable.")
    except Exception as error:
        raise BenchmarkCLIError("Redis is unavailable.") from error
    finally:
        await client.aclose()


async def _wait_for_terminal(repository: SqlAlchemyBenchmarkRepository, run_id: UUID) -> None:
    last_status: BenchmarkRunStatus | None = None
    while True:
        run = await repository.get_run(run_id)
        if run is None:
            raise BenchmarkCLIError(f"Benchmark disappeared: {run_id}")
        if run.status is not last_status:
            print(f"Status: {run.status.value}")
            last_status = run.status
        if run.status in {
            BenchmarkRunStatus.COMPLETED,
            BenchmarkRunStatus.PARTIAL,
            BenchmarkRunStatus.FAILED,
        }:
            return
        await asyncio.sleep(1)


def _print_plan(plan: BenchmarkPlan) -> None:
    print(f"Name: {plan.name}")
    print(f"Dataset: {plan.dataset_id}@{plan.dataset_version}")
    print(f"Dataset fingerprint: {plan.dataset_fingerprint}")
    print(f"Tasks: {plan.task_count}")
    print(f"Models: {plan.model_count}")
    print(f"Samples/task: {plan.samples_per_task}")
    print(
        "Planned generations: "
        f"{plan.task_count} x {plan.model_count} x {plan.samples_per_task} = "
        f"{plan.planned_generations}"
    )
    print(f"AI evaluation: {'enabled' if plan.ai_evaluation_enabled else 'disabled'}")
    print(
        f"Known pricing: {plan.model_count - len(plan.unknown_pricing)}/{plan.model_count} models"
    )
    if plan.estimated_maximum_costs:
        print(f"Estimated maximum generation cost: {_render_costs(plan.estimated_maximum_costs)}")
    else:
        print("Estimated maximum generation cost: unknown")
    if plan.unknown_pricing:
        print("Unknown pricing: " + ", ".join(plan.unknown_pricing))
    print(f"Estimate basis: {plan.estimate_basis}")
    if plan.warnings:
        print("Warnings:")
        for warning in plan.warnings:
            print(f"- {warning}")


def _required_services(settings: Settings) -> None:
    _required_database(settings)
    if not settings.persistence_enabled:
        raise BenchmarkCLIError("PERSISTENCE_ENABLED=true is required for benchmark execution.")


def _required_database(settings: Settings) -> str:
    if settings.database_url is None:
        raise BenchmarkCLIError("DATABASE_URL is required.")
    return settings.database_url


def _render_costs(costs: dict[str, Decimal]) -> str:
    return ", ".join(f"{currency} {amount}" for currency, amount in sorted(costs.items()))


def _generated_directory(run_id: UUID) -> Path:
    return Path("benchmark-results") / "generated" / str(run_id)


if __name__ == "__main__":
    main()
