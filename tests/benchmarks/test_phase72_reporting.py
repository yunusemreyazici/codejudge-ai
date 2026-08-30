from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.exporting import (
    BenchmarkExporter,
    BenchmarkExportError,
    render_report,
    write_export,
)
from app.benchmarks.models import (
    BenchmarkModelConfig,
    BenchmarkRun,
    BenchmarkRunStatus,
    BenchmarkSample,
    BenchmarkSampleStatus,
    GeneratedSolutionArtifact,
    PricingSnapshot,
)
from app.benchmarks.reliability import encode_failure_diagnostic
from app.benchmarks.repositories import BenchmarkResultRow
from app.snapshots.fingerprints import source_identity
from app.tasks.registry import TaskRegistry
from tests.database.helpers import snapshot_fixture

NOW = datetime(2026, 8, 29, tzinfo=UTC)
SOURCE = "class LRUCache:\n    pass\n"


class FakeBenchmarkRepository:
    def __init__(self, run: BenchmarkRun, rows: list[BenchmarkResultRow]) -> None:
        self.run = run
        self.rows = rows

    async def get_run(self, run_id: UUID) -> BenchmarkRun | None:
        return self.run if self.run.benchmark_run_id == run_id else None

    async def result_rows(self, run_id: UUID, **_: object) -> list[BenchmarkResultRow]:
        return self.rows if self.run.benchmark_run_id == run_id else []


class FakeEvaluationRepository:
    def __init__(self, snapshots: dict[UUID, object]) -> None:
        self.snapshots = snapshots

    async def get(self, evaluation_id: UUID) -> object | None:
        return self.snapshots.get(evaluation_id)


def _fixture() -> tuple[BenchmarkRun, list[BenchmarkResultRow], dict[UUID, object]]:
    tasks = TaskRegistry.default()
    dataset = BenchmarkDatasetRegistry.default(tasks).get("codejudge-core", "1")
    run_id = uuid4()
    good = _config(run_id, "good", 0, pricing=True)
    refusal = _config(run_id, "refusal", 1, pricing=False)
    completed_id = uuid4()
    completed = _row(good, completed_id, BenchmarkSampleStatus.COMPLETED, source=SOURCE)
    refused = _row(refusal, uuid4(), BenchmarkSampleStatus.GENERATION_FAILED)
    snapshot = snapshot_fixture(source=SOURCE, evaluation_id=completed_id, created_at=NOW)
    snapshot = snapshot.model_copy(
        update={"tests": snapshot.tests.model_copy(update={"passed": 8, "failed": 0, "total": 8})}
    )
    completed = BenchmarkResultRow(
        sample=completed.sample.model_copy(
            update={
                "task_version": snapshot.task_version,
                "task_fingerprint": snapshot.task_fingerprint,
                "tests_fingerprint": snapshot.tests_fingerprint,
            }
        ),
        config=completed.config,
        artifact=completed.artifact,
        deterministic_score=snapshot.final_score,
        ai_score=None,
        judge_score=None,
        adversarial_robustness=None,
        ai_status=None,
    )
    run = BenchmarkRun(
        benchmark_run_id=run_id,
        created_at=NOW,
        started_at=NOW,
        completed_at=NOW,
        status=BenchmarkRunStatus.COMPLETED,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        dataset_fingerprint=dataset.dataset_fingerprint,
        benchmark_policy_version="1",
        coding_prompt_version="1",
        coding_prompt_hash=good.coding_prompt_hash,
        evaluator_fingerprint="e" * 64,
        benchmark_run_fingerprint="f" * 64,
        samples_per_task=1,
        planned_sample_count=2,
        request_fingerprint="a" * 64,
        model_configs=(good, refusal),
    )
    return run, [completed, refused], {completed_id: snapshot}


def _config(run_id: UUID, model: str, ordinal: int, *, pricing: bool) -> BenchmarkModelConfig:
    return BenchmarkModelConfig(
        model_config_id=uuid4(),
        benchmark_run_id=run_id,
        ordinal=ordinal,
        provider_id="fake",
        model=model,
        display_name=model,
        temperature=0,
        top_p=1,
        max_output_tokens=100,
        output_mode="raw_source" if model == "refusal" else "structured_json",
        request_timeout_seconds=120 if model == "refusal" else 30,
        coding_prompt_hash="b" * 64,
        model_configuration_fingerprint=("c" if ordinal == 0 else "d") * 64,
        pricing=(
            PricingSnapshot(
                pricing_version="fake-v1",
                input_cost_per_million_tokens=Decimal("1"),
                output_cost_per_million_tokens=Decimal("4"),
                currency="USD",
            )
            if pricing
            else None
        ),
    )


def _row(
    config: BenchmarkModelConfig,
    evaluation_id: UUID,
    status: BenchmarkSampleStatus,
    *,
    source: str | None = None,
) -> BenchmarkResultRow:
    sample_id = uuid4()
    artifact = None
    if source is not None:
        source_hash, source_size = source_identity(source)
        artifact = GeneratedSolutionArtifact(
            benchmark_sample_id=sample_id,
            source=source,
            source_hash=source_hash,
            source_size=source_size,
            input_tokens=100,
            output_tokens=20,
            generation_latency_ms=250,
            pricing_version="fake-v1",
            generation_cost=Decimal("0.000180000000"),
            currency="USD",
            created_at=NOW,
        )
    return BenchmarkResultRow(
        sample=BenchmarkSample(
            benchmark_sample_id=sample_id,
            benchmark_run_id=config.benchmark_run_id,
            model_config_id=config.model_config_id,
            evaluation_id=evaluation_id,
            task_id="lru-cache",
            task_version="1",
            task_fingerprint="1" * 64,
            tests_fingerprint="2" * 64,
            task_weight=1,
            sample_index=1,
            status=status,
            attempt_count=1,
            max_attempts=3,
            failure_code=encode_failure_diagnostic("provider_refusal", "refusal")
            if status is BenchmarkSampleStatus.GENERATION_FAILED
            else None,
            evaluation_duration_seconds=0.75 if source is not None else None,
            total_duration_seconds=1 if source is not None else None,
            created_at=NOW,
            updated_at=NOW,
            completed_at=NOW,
        ),
        config=config,
        artifact=artifact,
        deterministic_score=None,
        ai_score=None,
        judge_score=None,
        adversarial_robustness=None,
        ai_status=None,
    )


def _exporter(
    run: BenchmarkRun, rows: list[BenchmarkResultRow], snapshots: dict[UUID, object]
) -> BenchmarkExporter:
    return BenchmarkExporter(
        FakeBenchmarkRepository(run, rows),  # type: ignore[arg-type]
        FakeEvaluationRepository(snapshots),  # type: ignore[arg-type]
        BenchmarkDatasetRegistry.default(TaskRegistry.default()),
    )


async def test_export_is_deterministic_auditable_and_report_is_structural(tmp_path: Path) -> None:
    run, rows, snapshots = _fixture()
    exporter = _exporter(run, rows, snapshots)

    first = await exporter.build(run.benchmark_run_id, secret_values=())
    second = await exporter.build(run.benchmark_run_id, secret_values=())
    report = render_report(first)

    assert first.results_bytes == second.results_bytes
    assert first.results_sha256 == hashlib.sha256(first.results_bytes).hexdigest()
    assert first.document["dataset"]["fingerprint"] == run.dataset_fingerprint
    assert first.document["schema_version"] == "2"
    assert first.document["models"][0]["model_configuration_fingerprint"]
    assert first.document["totals"]["provider_refusals"] == 1
    assert first.document["models"][1]["actual_generation_costs"] == {}
    assert first.document["models"][0]["generation_reliability"] == {
        "planned_generations": 1,
        "successful_generations": 1,
        "generation_failures": 0,
        "generation_success_rate": 1,
        "failure_categories": {},
        "failure_details": {},
    }
    assert first.document["models"][1]["generation_reliability"] == {
        "planned_generations": 1,
        "successful_generations": 0,
        "generation_failures": 1,
        "generation_success_rate": 0,
        "failure_categories": {"provider_error": 1},
        "failure_details": {"provider_error": {"refusal": 1}},
    }
    refused_sample = next(
        sample for sample in first.document["samples"] if sample["status"] == "generation_failed"
    )
    assert refused_sample["failure_code"] == "provider_refusal"
    assert refused_sample["failure_detail_code"] == "refusal"
    assert first.document["models"][1]["generation_parameters"]["output_mode"] == "raw_source"
    assert first.document["evaluator"]["ai_cost"]["status"] == "not_applicable"
    assert first.document["samples"][0]["evaluation"]["score_breakdown"]["correctness"] == 75
    assert first.document["samples"][0]["evaluation"]["tests"]["total"] == 8
    assert first.document["samples"][0]["evaluation"]["test_execution_seconds"] == 0.4
    assert first.document["samples"][0]["evaluation"]["evaluation_lifecycle_seconds"] == 0.75
    assert "duration_seconds" not in first.document["samples"][0]["evaluation"]
    good_metrics = first.document["leaderboard"][0]
    assert good_metrics["correctness_pass_rate"] == 1
    assert good_metrics["end_to_end_success_rate"] == 1
    assert good_metrics["perfect_deterministic_score_rate"] == 0
    assert good_metrics["mean_test_execution_seconds"] == 0.4
    assert good_metrics["mean_evaluation_lifecycle_seconds"] == 0.75
    assert first.document["models"][0]["cost_per_successful_generation"] == {
        "USD": Decimal("0.000180000000")
    }
    assert first.document["models"][0]["cost_per_correct_evaluation"] == {
        "USD": Decimal("0.000180000000")
    }
    assert (
        first.document["models"][0]["deterministic_score_distribution"]["standard_deviation"]
        is None
    )
    assert first.document["models"][0]["confidence_interval_95"] is None
    assert first.document["models"][0]["stability_label"] == "not_enough_samples"
    assert first.document["models"][0]["generation_cost_distributions"]["USD"] == {
        "count": 1,
        "mean": 0.00018,
        "median": 0.00018,
        "standard_deviation": None,
        "minimum": 0.00018,
        "maximum": 0.00018,
    }
    assert first.document["per_task"][0]["score_standard_deviation"] is None
    assert "These results apply to the exact dataset" in report
    assert "unknown" in report
    assert "Failures & Refusals" in report
    assert "Per-Task Results" in report
    assert first.results_sha256 in report
    assert "AI score" in report
    assert "Coverage-adjusted score" in report
    assert "Correctness pass" in report
    assert "Evaluation lifecycle mean" in report
    assert "provider_refusal" in report
    assert "mixes generation output modes" in report
    assert "Repeated-Sample Statistics" in report
    assert "Stability" in report
    assert "Correctness Consistency" in report
    assert "Most Variable Tasks" in report
    assert "Cost Distribution" in report
    assert "Latency Distribution" in report
    assert "Generation Reliability" in report
    assert "Generation Failure Diagnostics" in report
    assert "provider_error=1" in report
    assert "refusal" in report
    assert "not enough samples" in report

    output = tmp_path / "run" / "results.json"
    write_export(first, output)
    assert output.read_bytes() == first.results_bytes
    candidate = next((output.parent / "candidates").iterdir())
    assert candidate.name.endswith(".py")
    assert candidate.read_text(encoding="utf-8") == SOURCE


async def test_export_rejects_source_hash_mismatch_and_known_secrets() -> None:
    run, rows, snapshots = _fixture()
    artifact = rows[0].artifact
    assert artifact is not None
    tampered = artifact.model_copy(update={"source_hash": "0" * 64})
    rows[0] = BenchmarkResultRow(
        sample=rows[0].sample,
        config=rows[0].config,
        artifact=tampered,
        deterministic_score=rows[0].deterministic_score,
        ai_score=None,
        judge_score=None,
        adversarial_robustness=None,
        ai_status=None,
    )
    with pytest.raises(BenchmarkExportError, match="integrity check failed"):
        await _exporter(run, rows, snapshots).build(run.benchmark_run_id, secret_values=())

    run, rows, snapshots = _fixture()
    artifact = rows[0].artifact
    assert artifact is not None
    leaked_source = artifact.source + "\nsecret = 'configured-secret-value'\n"
    leaked_hash, leaked_size = source_identity(leaked_source)
    leaked_artifact = artifact.model_copy(
        update={"source": leaked_source, "source_hash": leaked_hash, "source_size": leaked_size}
    )
    rows[0] = BenchmarkResultRow(
        sample=rows[0].sample,
        config=rows[0].config,
        artifact=leaked_artifact,
        deterministic_score=rows[0].deterministic_score,
        ai_score=None,
        judge_score=None,
        adversarial_robustness=None,
        ai_status=None,
    )
    snapshots = {}
    rows[0] = BenchmarkResultRow(
        sample=rows[0].sample.model_copy(
            update={"status": BenchmarkSampleStatus.EVALUATION_FAILED}
        ),
        config=rows[0].config,
        artifact=rows[0].artifact,
        deterministic_score=None,
        ai_score=None,
        judge_score=None,
        adversarial_robustness=None,
        ai_status=None,
    )
    with pytest.raises(BenchmarkExportError, match="configured secret"):
        await _exporter(run, rows, snapshots).build(
            run.benchmark_run_id, secret_values=("configured-secret-value",)
        )


async def test_incomplete_requires_opt_in_and_failed_run_has_no_leaderboard() -> None:
    run, rows, snapshots = _fixture()
    queued = run.model_copy(update={"status": BenchmarkRunStatus.RUNNING, "completed_at": None})
    exporter = _exporter(queued, rows, snapshots)
    with pytest.raises(BenchmarkExportError, match="--allow-incomplete"):
        await exporter.build(run.benchmark_run_id, secret_values=())
    incomplete = await exporter.build(run.benchmark_run_id, allow_incomplete=True, secret_values=())
    assert incomplete.document["run"]["incomplete"] is True

    failed = run.model_copy(update={"status": BenchmarkRunStatus.FAILED})
    diagnostic = await _exporter(failed, rows, snapshots).build(
        run.benchmark_run_id, secret_values=()
    )
    assert diagnostic.document["leaderboard"] == []
    assert "No leaderboard is shown" in render_report(diagnostic)

    no_measurements = await _exporter(run, rows[1:], {}).build(
        run.benchmark_run_id, secret_values=()
    )
    assert no_measurements.document["run"]["meaningful_results"] is False
    assert no_measurements.document["leaderboard"] == []
