"""Single CLI for planning, running, inspecting, and exporting benchmarks."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
from redis.asyncio import Redis

from app.ai.factory import create_ai_service
from app.ai.models import ProviderResponse, StructuredLLMRequest
from app.ai.providers.base import ProviderError
from app.ai.providers.openai_compatible import OpenAICompatibleProvider
from app.analysis.factory import create_static_analysis_engine
from app.benchmarks.datasets import BenchmarkDatasetRegistry, DatasetRegistryError
from app.benchmarks.exporting import (
    BenchmarkArtifacts,
    BenchmarkExporter,
    BenchmarkExportError,
    render_report,
    write_export,
)
from app.benchmarks.models import (
    BenchmarkCreateRequest,
    BenchmarkRunStatus,
    BenchmarkSampleStatus,
    CodingOutput,
    GenerationOutputMode,
)
from app.benchmarks.productization import (
    BenchmarkProductError,
    build_comparison,
    build_run_listing,
    comparison_json_bytes,
    parse_dataset_selector,
    render_comparison_markdown,
    render_run_listing,
    render_run_show,
    verify_archive,
    write_archive,
    write_comparison,
)
from app.benchmarks.prompts import coding_payload, coding_system_prompt
from app.benchmarks.repositories import SqlAlchemyBenchmarkRepository
from app.benchmarks.run_config import (
    BenchmarkPlan,
    BenchmarkRunConfig,
    build_plan,
    load_benchmark_config,
    resolved_provider_values,
    validate_run_preflight,
)
from app.benchmarks.service import BenchmarkError, BenchmarkService
from app.core.config import DEFAULT_LLM_MAX_RESPONSE_BYTES, Settings
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
    probe = commands.add_parser("probe", help="make one sanitized provider diagnostic request")
    probe.add_argument("config", type=Path)
    probe.add_argument("--model", required=True)
    probe.add_argument("--show-content", action="store_true")
    status = commands.add_parser("status", help="show durable benchmark progress")
    status.add_argument("run_id", type=UUID)
    listing = commands.add_parser("list", help="list persisted benchmark runs")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--dataset")
    show = commands.add_parser("show", help="show a persisted benchmark summary")
    show.add_argument("run_id", type=UUID)
    export = commands.add_parser("export", help="write deterministic machine-readable results")
    export.add_argument("run_id", type=UUID)
    export.add_argument("--format", choices=("json",), default="json")
    export.add_argument("--output", type=Path)
    export.add_argument("--allow-incomplete", action="store_true")
    report = commands.add_parser("report", help="write a self-contained Markdown report")
    report.add_argument("run_id", type=UUID)
    report.add_argument("--output", type=Path)
    report.add_argument("--allow-incomplete", action="store_true")
    compare = commands.add_parser("compare", help="compare two compatible persisted runs")
    compare.add_argument("run_a", type=UUID)
    compare.add_argument("run_b", type=UUID)
    compare.add_argument("--json", action="store_true", dest="json_output")
    compare.add_argument("--output", type=Path)
    archive = commands.add_parser("archive", help="create an immutable local run archive")
    archive.add_argument("run_id", type=UUID)
    archive.add_argument("--output", type=Path)
    verify = commands.add_parser("verify-archive", help="verify a local archive without a database")
    verify.add_argument("path", type=Path)
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
        BenchmarkProductError,
        ProviderError,
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
    if arguments.command == "probe":
        return await _probe(
            arguments.config,
            model_name=arguments.model,
            show_content=arguments.show_content,
        )
    if arguments.command == "status":
        await _status(arguments.run_id)
        return 0
    if arguments.command == "list":
        await _list(arguments.limit, arguments.dataset)
        return 0
    if arguments.command == "show":
        artifacts = await _export(arguments.run_id, allow_incomplete=True)
        print(render_run_show(artifacts))
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
    if arguments.command == "compare":
        return await _compare(
            arguments.run_a,
            arguments.run_b,
            json_output=arguments.json_output,
            output=arguments.output,
        )
    if arguments.command == "archive":
        output = arguments.output or _archive_directory(arguments.run_id)
        artifacts = await _export(arguments.run_id, allow_incomplete=False)
        manifest = write_archive(artifacts, output)
        print(f"Archive: {output}")
        print(f"Run ID: {manifest['run_id']}")
        print(f"Results SHA-256: {manifest['results_sha256']}")
        return 0
    if arguments.command == "verify-archive":
        manifest = verify_archive(arguments.path)
        print(f"Archive verified: {arguments.path}")
        print(f"Run ID: {manifest['run_id']}")
        print(f"Results SHA-256: {manifest['results_sha256']}")
        return 0
    raise BenchmarkCLIError("Unknown command.")


async def _probe(
    config_path: Path,
    *,
    model_name: str,
    show_content: bool,
    environment: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Issue exactly one generation request without creating benchmark state."""
    config = load_benchmark_config(config_path)
    matches = [model for model in config.resolved_models() if model.model == model_name]
    if len(matches) != 1:
        qualifier = "not configured" if not matches else "configured more than once"
        raise BenchmarkCLIError(f"Probe model is {qualifier}: {model_name}")
    model = matches[0]
    base_url, credential, timeout_seconds, max_concurrent_requests = resolved_provider_values(
        config, environment
    )[model.provider_id]
    tasks = TaskRegistry.default()
    datasets = BenchmarkDatasetRegistry.default(tasks)
    dataset = datasets.get(config.dataset.id, config.dataset.version)
    task = datasets.resolve_task(dataset.task_entries[0]).specification
    provider = OpenAICompatibleProvider(
        base_url=base_url,
        api_key=credential,
        timeout_seconds=timeout_seconds,
        max_attempts=1,
        max_response_bytes=DEFAULT_LLM_MAX_RESPONSE_BYTES,
        max_concurrent_requests=max_concurrent_requests,
        client=client,
    )
    request = StructuredLLMRequest(
        component="coding_generation_probe",
        model=model.model,
        system_prompt=coding_system_prompt(model.output_mode),
        input_payload=coding_payload(task, model.output_mode),
        response_schema=CodingOutput.model_json_schema(),
        max_output_tokens=model.max_output_tokens,
        temperature=model.temperature,
        top_p=model.top_p,
        seed=model.seed,
    )
    try:
        response = (
            await provider.complete_raw_source(request)
            if model.output_mode is GenerationOutputMode.RAW_SOURCE
            else await provider.complete_structured(request)
        )
    finally:
        await provider.close()
    _print_probe_response(response, show_content=show_content)
    return 0


def _print_probe_response(response: ProviderResponse, *, show_content: bool) -> None:
    diagnostics = response.diagnostics
    if diagnostics is None:
        raise BenchmarkCLIError("Provider response diagnostics are unavailable.")
    print(f"HTTP status: {diagnostics.http_status}")
    print(f"Envelope type: {diagnostics.envelope_type}")
    print(f"Choices count: {diagnostics.choices_count}")
    print(f"Finish reason: {diagnostics.finish_reason or 'unknown'}")
    print(f"Content type: {diagnostics.content_type}")
    print(f"Content length: {diagnostics.content_length}")
    print(f"Usage presence: {'yes' if diagnostics.usage_present else 'no'}")
    print(f"Latency: {response.latency_ms} ms")
    print(f"Provider response model: {diagnostics.provider_response_model or 'unknown'}")
    if show_content:
        print("Content (JSON string):")
        print(json.dumps(response.content, ensure_ascii=False))


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
                models=list(config.resolved_models()),
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


async def _list(limit: int, dataset_selector: str | None) -> None:
    dataset_id, dataset_version = parse_dataset_selector(dataset_selector)
    settings = Settings.from_env()
    database = Database(_required_database(settings))
    try:
        repository = SqlAlchemyBenchmarkRepository(database.session_factory)
        rows = await build_run_listing(
            repository,
            limit=limit,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
        print(render_run_listing(rows))
    finally:
        await database.dispose()


async def _compare(
    run_a: UUID,
    run_b: UUID,
    *,
    json_output: bool,
    output: Path | None,
) -> int:
    if run_a == run_b:
        raise BenchmarkCLIError("Comparison requires two distinct benchmark runs.")
    if output is not None and json_output and output.suffix.lower() != ".json":
        raise BenchmarkCLIError("--json requires a .json output path when --output is used.")
    artifacts_a = await _export(run_a, allow_incomplete=False)
    artifacts_b = await _export(run_b, allow_incomplete=False)
    comparison = build_comparison(artifacts_a, artifacts_b)
    if output is not None:
        write_comparison(comparison, output)
        print(f"Comparison: {output}")
    elif json_output:
        sys.stdout.write(comparison_json_bytes(comparison).decode("utf-8"))
    else:
        sys.stdout.write(render_comparison_markdown(comparison))
    if comparison["compatibility"]["status"] == "incompatible":
        print(
            "error: benchmark runs are incompatible; no metric deltas were produced",
            file=sys.stderr,
        )
        return 2
    return 0


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
    for model in plan.models:
        print(
            f"Generation transport ({model.provider_id}/{model.model}): "
            f"{model.output_mode.value}, timeout {model.request_timeout_seconds:g}s, "
            "provider concurrency "
            f"{model.max_concurrent_requests or 'unlimited'}"
        )
        estimate = (
            "unknown"
            if model.estimated_maximum_cost is None or model.currency is None
            else f"{model.currency} {model.estimated_maximum_cost}"
        )
        print(
            f"Estimated maximum ({model.provider_id}/{model.model}, "
            f"{model.planned_generations} generations): {estimate}"
        )
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


def _archive_directory(run_id: UUID) -> Path:
    return Path("benchmark-results") / "runs" / str(run_id)


if __name__ == "__main__":
    main()
