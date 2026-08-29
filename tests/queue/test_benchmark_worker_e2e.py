import asyncio
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.ai.providers.base import ProviderError
from app.analysis.factory import create_static_analysis_engine
from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.models import DatasetTaskEntry, PricingSnapshot
from app.benchmarks.pricing import PricingCatalog
from app.benchmarks.queue import BenchmarkOutboxPublisher, BenchmarkQueue
from app.benchmarks.service import BenchmarkService
from app.benchmarks.worker import BenchmarkWorker
from app.core.config import Settings
from app.evaluator.engine import EvaluationEngine
from app.evaluator.service import EvaluationService
from app.main import create_app
from app.runners.docker_cli import DockerCli
from app.runners.docker_runner import DockerPythonRunner
from app.runners.factory import create_python_runner
from app.snapshots.fingerprints import task_fingerprint
from app.snapshots.fingerprints import tests_fingerprint as _tests_fingerprint
from app.snapshots.metadata import ExecutionMetadataCollector
from app.tasks.registry import TaskRegistry
from tests.ai.fakes import FakeProvider, TaskAwareFakeProvider
from tests.conftest import CORRECT_LRU, INCORRECT_LRU
from tests.database.conftest import DatabaseHarness
from tests.queue.conftest import RedisHarness
from tests.tasks.candidates import INCORRECT_CANDIDATES

pytestmark = [
    pytest.mark.database,
    pytest.mark.queue,
    pytest.mark.sandbox,
    pytest.mark.worker_e2e,
    pytest.mark.benchmark_e2e,
]


async def test_phase72_packaged_cli_fake_provider_publish_flow(
    database_harness: DatabaseHarness,
    redis_harness: RedisHarness,
    tmp_path: Path,
) -> None:
    del redis_harness
    database_url = os.environ["CODEJUDGE_TEST_DATABASE_URL"]
    redis_url = os.environ["CODEJUDGE_TEST_REDIS_URL"]
    config_path = tmp_path / "fake-cli.yaml"
    config_path.write_text(
        """\
schema_version: "1"
name: phase72-fake-cli
dataset:
  id: codejudge-core
  version: "1"
samples_per_task: 1
models:
  - {provider_id: fake, model: good, temperature: 0, max_output_tokens: 1000}
  - {provider_id: fake, model: bad, temperature: 0, max_output_tokens: 1000}
  - {provider_id: fake, model: refusal, temperature: 0, max_output_tokens: 1000}
providers:
  fake:
    protocol: openai-compatible
    base_url_env: CODEJUDGE_FAKE_BASE_URL
    credential_env: CODEJUDGE_FAKE_API_KEY
ai_evaluation:
  enabled: false
pricing:
  fake/good: {version: fake-ci-v1, currency: USD, input_per_million: 2, output_per_million: 8}
  fake/bad: {version: fake-ci-v1, currency: USD, input_per_million: 2, output_per_million: 8}
""",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PERSISTENCE_ENABLED": "true",
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "LOG_LEVEL": "CRITICAL",
        "CODEJUDGE_FAKE_BASE_URL": "http://127.0.0.1:1/v1",
        "CODEJUDGE_FAKE_API_KEY": "fake-cli-generation-secret",
    }

    plan = await _packaged_cli(environment, "plan", str(config_path))
    assert "Planned generations: 1 x 3 x 1 = 3" in plan
    assert "Known pricing: 2/3 models" in plan
    assert "Unknown pricing: fake/refusal" in plan

    accepted = await _packaged_cli(environment, "run", str(config_path))
    run_id = UUID(
        next(
            line.removeprefix("Run ID: ")
            for line in accepted.splitlines()
            if line.startswith("Run ID: ")
        )
    )
    assert "Benchmark accepted" in accepted
    assert "Planned samples: 3" in accepted

    tasks = TaskRegistry.default()
    settings = Settings(
        log_level="CRITICAL",
        persistence_enabled=True,
        database_url=database_url,
    )
    runner = create_python_runner(settings)
    evaluations = EvaluationService(
        EvaluationEngine(
            registry=tasks,
            runners={"python": runner},
            max_code_size=settings.max_code_size,
            analysis_engine=create_static_analysis_engine(settings),
        ),
        ExecutionMetadataCollector(settings),
        database_harness.repository,
    )
    datasets = BenchmarkDatasetRegistry.default(tasks)
    identity = uuid4().hex
    queue = BenchmarkQueue(
        redis_url,
        stream=f"codejudge:benchmark-phase72:{identity}",
        group=f"codejudge-benchmark-phase72-{identity}",
    )
    await queue.ensure_group()
    publisher = BenchmarkOutboxPublisher(
        database_harness.benchmark_repository,
        queue,
        retry_base_delay_seconds=0.01,
    )
    assert await publisher.dispatch_once() == 3
    provider = FakeProvider()
    provider.add("coding_generation", "good", [{"language": "python", "source": CORRECT_LRU}])
    provider.add("coding_generation", "bad", [{"language": "python", "source": INCORRECT_LRU}])
    provider.add("coding_generation", "refusal", [ProviderError("provider_refusal")])
    worker = BenchmarkWorker(
        worker_id="phase72-cli-e2e",
        providers={"fake": provider},
        repository=database_harness.benchmark_repository,
        queue=queue,
        datasets=datasets,
        tasks=tasks,
        evaluations=evaluations,
        max_code_size=settings.max_code_size,
        lease_seconds=10,
        retry_base_delay_seconds=0.01,
    )
    for _ in range(3):
        message = await queue.consume("phase72-cli-e2e", block_ms=100)
        assert message is not None
        await worker.process_message(message)
    await queue.close()

    status = await _packaged_cli(environment, "status", str(run_id))
    assert "Status: completed" in status
    assert "Completed: 2" in status
    assert "Generation failures: 1" in status
    assert "Current generation cost:" in status

    output_directory = tmp_path / "artifacts"
    results_path = output_directory / "results.json"
    report_path = output_directory / "report.md"
    exported = await _packaged_cli(
        environment, "export", str(run_id), "--output", str(results_path)
    )
    reported = await _packaged_cli(environment, "report", str(run_id), "--output", str(report_path))
    assert "SHA-256:" in exported
    assert "Results SHA-256:" in reported
    results = json.loads(results_path.read_text(encoding="utf-8"))
    report = report_path.read_text(encoding="utf-8")
    assert [entry["model"] for entry in results["leaderboard"]] == ["good", "bad", "refusal"]
    assert results["totals"]["provider_refusals"] == 1
    assert results["models"][2]["actual_generation_costs"] == {}
    assert results["evaluator"]["ai_enabled"] is False
    assert len(list((output_directory / "candidates").glob("*.py"))) == 2
    assert "fake-cli-generation-secret" not in results_path.read_text(encoding="utf-8")
    assert "These results apply to the exact dataset" in report
    assert "- AI evaluation: disabled" in report
    assert "provider_refusal" in report
    assert "unknown" in report


async def _packaged_cli(environment: dict[str, str], *arguments: str) -> str:
    executable = Path(sys.executable).with_name("codejudge-benchmark")
    assert executable.is_file()
    process = await asyncio.create_subprocess_exec(
        str(executable),
        *arguments,
        cwd=Path.cwd(),
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
    return stdout.decode("utf-8")


async def test_phase7_fake_models_real_postgres_redis_docker_benchmark_e2e(
    database_harness: DatabaseHarness,
    redis_harness: RedisHarness,
) -> None:
    del redis_harness
    database_url = os.environ["CODEJUDGE_TEST_DATABASE_URL"]
    redis_url = os.environ["CODEJUDGE_TEST_REDIS_URL"]
    settings = Settings(
        log_level="CRITICAL",
        persistence_enabled=True,
        database_url=database_url,
    )
    tasks = TaskRegistry.default()
    runner = create_python_runner(settings)
    assert isinstance(runner, DockerPythonRunner)
    capability = await runner.check_capability()
    if not capability.available:
        diagnostic = f"reason={capability.reason or 'unknown'} detail={capability.detail}"
        if os.getenv("CODEJUDGE_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker sandbox is required: {diagnostic}")
        pytest.skip(diagnostic)
    evaluations = EvaluationService(
        EvaluationEngine(
            registry=tasks,
            runners={"python": runner},
            max_code_size=settings.max_code_size,
            analysis_engine=create_static_analysis_engine(settings),
        ),
        ExecutionMetadataCollector(settings),
        database_harness.repository,
    )
    pricing = PricingCatalog(
        {
            ("fake", model): PricingSnapshot(
                pricing_version="fake-ci-v1",
                input_cost_per_million_tokens=Decimal("2"),
                output_cost_per_million_tokens=Decimal("8"),
                currency="USD",
            )
            for model in ("good", "bad", "refusal")
        }
    )
    datasets = BenchmarkDatasetRegistry.default(tasks)
    benchmarks = BenchmarkService(
        database_harness.benchmark_repository,
        datasets,
        tasks,
        evaluations,
        pricing=pricing,
    )
    application = create_app(
        settings=Settings(log_level="CRITICAL"),
        registry=tasks,
        benchmark_service=benchmarks,
    )
    payload = {
        "dataset_id": "codejudge-core",
        "dataset_version": "1",
        "models": [
            {"provider_id": "fake", "model": "good", "temperature": 0},
            {"provider_id": "fake", "model": "bad", "temperature": 0},
            {"provider_id": "fake", "model": "refusal", "temperature": 0},
        ],
        "samples_per_task": 1,
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/benchmarks", json=payload)
    assert response.status_code == 202
    run_id = UUID(response.json()["benchmark_run_id"])

    identity = uuid4().hex
    queue = BenchmarkQueue(
        redis_url,
        stream=f"codejudge:benchmark-e2e:{identity}",
        group=f"codejudge-benchmark-e2e-{identity}",
    )
    await queue.ensure_group()
    publisher = BenchmarkOutboxPublisher(
        database_harness.benchmark_repository,
        queue,
        retry_base_delay_seconds=0.01,
    )
    assert await publisher.dispatch_once() == 3
    provider = FakeProvider()
    provider.add("coding_generation", "good", [{"language": "python", "source": CORRECT_LRU}])
    provider.add("coding_generation", "bad", [{"language": "python", "source": INCORRECT_LRU}])
    provider.add("coding_generation", "refusal", [ProviderError("provider_refusal")])
    worker = BenchmarkWorker(
        worker_id="benchmark-e2e",
        providers={"fake": provider},
        repository=database_harness.benchmark_repository,
        queue=queue,
        datasets=datasets,
        tasks=tasks,
        evaluations=evaluations,
        max_code_size=settings.max_code_size,
        lease_seconds=10,
        retry_base_delay_seconds=0.01,
    )
    for _ in range(3):
        message = await queue.consume("benchmark-e2e", block_ms=100)
        assert message is not None
        await worker.process_message(message)

    restarted = create_app(
        settings=Settings(log_level="CRITICAL"),
        registry=tasks,
        benchmark_service=benchmarks,
    )
    provider_calls = len(provider.requests)
    async with AsyncClient(
        transport=ASGITransport(app=restarted), base_url="http://test"
    ) as client:
        summary = await client.get(f"/api/v1/benchmarks/{run_id}")
        leaderboard = await client.get(f"/api/v1/benchmarks/{run_id}/leaderboard")
        samples = await client.get(f"/api/v1/benchmarks/{run_id}/samples")
        completed_sample = next(
            sample for sample in samples.json() if sample["status"] == "completed"
        )
        detail = await client.get(
            f"/api/v1/benchmarks/{run_id}/samples/{completed_sample['benchmark_sample_id']}"
        )

    assert summary.json()["status"] == "completed"
    assert summary.json()["completed_samples"] == 2
    assert summary.json()["generation_failures"] == 1
    entries = leaderboard.json()
    assert [entry["model"] for entry in entries] == ["good", "bad", "refusal"]
    assert entries[0]["weighted_mean_score"] == 100
    assert entries[1]["weighted_mean_score"] < 100
    assert entries[2]["coverage"] == 0
    assert entries[2]["generation_failure_rate"] == 1
    refusal = next(sample for sample in samples.json() if sample["model"] == "refusal")
    assert refusal["deterministic_score"] is None
    assert refusal["failure_code"] == "provider_refusal"
    assert detail.json()["source_hash"]
    assert detail.json()["evaluation_id"]
    assert detail.json()["pricing_version"] == "fake-ci-v1"
    assert detail.json()["generation_cost"] == "0.000600000000"
    assert len(provider.requests) == provider_calls == 3
    assert await queue.pending_count() == 0

    remaining = await DockerCli().run(
        [
            "ps",
            "-a",
            "--filter",
            "label=codejudge.component=sandbox",
            "--format",
            "{{.Names}}",
        ],
        timeout_seconds=5,
        output_limit_bytes=4096,
    )
    assert remaining.exit_code == 0
    assert "codejudge-eval-" not in remaining.stdout
    await queue.close()


async def test_multitask_fake_models_real_services_and_static_analysis_e2e(
    database_harness: DatabaseHarness,
    redis_harness: RedisHarness,
    tmp_path: Path,
) -> None:
    del redis_harness
    database_url = os.environ["CODEJUDGE_TEST_DATABASE_URL"]
    redis_url = os.environ["CODEJUDGE_TEST_REDIS_URL"]
    settings = Settings(
        log_level="CRITICAL",
        persistence_enabled=True,
        database_url=database_url,
    )
    tasks = TaskRegistry.default()
    selected_ids = ("dependency-resolver", "retry-backoff", "ttl-cache")
    entries: list[DatasetTaskEntry] = []
    for task_id in selected_ids:
        task = tasks.get(task_id)
        tests_hash = _tests_fingerprint(task)
        entries.append(
            DatasetTaskEntry(
                task_id=task_id,
                task_version=task.specification.version,
                task_fingerprint=task_fingerprint(task, tests_hash),
                tests_fingerprint=tests_hash,
                weight=1,
            )
        )
    definition = {
        "dataset_id": "codejudge-ci-portfolio",
        "dataset_version": "1",
        "title": "Representative CI portfolio",
        "description": "Three-task deterministic cross-task benchmark fixture.",
        "task_entries": [entry.model_dump(mode="json") for entry in reversed(entries)],
    }
    (tmp_path / "portfolio.json").write_text(json.dumps(definition), encoding="utf-8")
    datasets = BenchmarkDatasetRegistry(tmp_path, tasks)
    datasets.load()

    runner = create_python_runner(settings)
    assert isinstance(runner, DockerPythonRunner)
    capability = await runner.check_capability()
    if not capability.available:
        diagnostic = f"reason={capability.reason or 'unknown'} detail={capability.detail}"
        if os.getenv("CODEJUDGE_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker sandbox is required: {diagnostic}")
        pytest.skip(diagnostic)
    evaluations = EvaluationService(
        EvaluationEngine(
            registry=tasks,
            runners={"python": runner},
            max_code_size=settings.max_code_size,
            analysis_engine=create_static_analysis_engine(settings),
        ),
        ExecutionMetadataCollector(settings),
        database_harness.repository,
    )
    benchmarks = BenchmarkService(
        database_harness.benchmark_repository,
        datasets,
        tasks,
        evaluations,
    )
    application = create_app(
        settings=Settings(log_level="CRITICAL"),
        registry=tasks,
        benchmark_service=benchmarks,
    )
    payload = {
        "dataset_id": "codejudge-ci-portfolio",
        "dataset_version": "1",
        "models": [
            {"provider_id": "fake", "model": "portfolio-good", "temperature": 0},
            {"provider_id": "fake", "model": "portfolio-bad", "temperature": 0},
        ],
        "samples_per_task": 1,
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/benchmarks", json=payload)
    assert response.status_code == 202
    run_id = UUID(response.json()["benchmark_run_id"])

    identity = uuid4().hex
    queue = BenchmarkQueue(
        redis_url,
        stream=f"codejudge:portfolio-e2e:{identity}",
        group=f"codejudge-portfolio-e2e-{identity}",
    )
    await queue.ensure_group()
    publisher = BenchmarkOutboxPublisher(
        database_harness.benchmark_repository,
        queue,
        retry_base_delay_seconds=0.01,
    )
    assert await publisher.dispatch_once() == 6
    outputs: dict[tuple[str, str], object] = {}
    for task_id in selected_ids:
        reference_path = tasks.get(task_id).reference_path
        assert reference_path is not None
        outputs[("portfolio-good", task_id)] = {
            "language": "python",
            "source": reference_path.read_text(encoding="utf-8"),
        }
        outputs[("portfolio-bad", task_id)] = {
            "language": "python",
            "source": INCORRECT_CANDIDATES[task_id],
        }
    provider = TaskAwareFakeProvider(outputs)
    worker = BenchmarkWorker(
        worker_id="portfolio-e2e",
        providers={"fake": provider},
        repository=database_harness.benchmark_repository,
        queue=queue,
        datasets=datasets,
        tasks=tasks,
        evaluations=evaluations,
        max_code_size=settings.max_code_size,
        lease_seconds=10,
        retry_base_delay_seconds=0.01,
    )
    for _ in range(6):
        message = await queue.consume("portfolio-e2e", block_ms=100)
        assert message is not None
        await worker.process_message(message)

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        summary = await client.get(f"/api/v1/benchmarks/{run_id}")
        leaderboard = await client.get(f"/api/v1/benchmarks/{run_id}/leaderboard")
        samples = await client.get(f"/api/v1/benchmarks/{run_id}/samples")

    assert summary.json()["status"] == "completed"
    assert summary.json()["planned_samples"] == 6
    assert summary.json()["completed_samples"] == 6
    entries = leaderboard.json()
    assert [entry["model"] for entry in entries] == ["portfolio-good", "portfolio-bad"]
    assert entries[0]["weighted_mean_score"] > entries[1]["weighted_mean_score"]
    assert entries[0]["coverage"] == entries[1]["coverage"] == 1
    assert {task["task_id"] for task in entries[0]["per_task"]} == set(selected_ids)
    assert len(samples.json()) == 6
    assert all(sample["evaluation_id"] for sample in samples.json())
    assert len(provider.requests) == 6
    requested_pairs = sorted(
        (request.model, request.input_payload["public_task"]["id"]) for request in provider.requests
    )
    assert requested_pairs == sorted(outputs)
    assert await queue.pending_count() == 0
    await queue.close()
