"""Deterministic, allowlisted benchmark result export and Markdown reporting."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from app.ai.models import AIStatus
from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.models import (
    CODING_PROMPT_VERSION,
    BenchmarkRunStatus,
    BenchmarkSampleStatus,
)
from app.benchmarks.prompts import CODING_PROMPT_HASH
from app.benchmarks.repositories import BenchmarkRepository, BenchmarkResultRow
from app.benchmarks.statistics import build_leaderboard
from app.db.repositories import EvaluationRepository
from app.snapshots.fingerprints import source_identity
from app.snapshots.models import EvaluationSnapshot

_TERMINAL_RUNS = {
    BenchmarkRunStatus.COMPLETED,
    BenchmarkRunStatus.PARTIAL,
    BenchmarkRunStatus.FAILED,
}
_SECRET_PATTERN = re.compile(r"(?i)(authorization\s*:\s*bearer\s+|bearer\s+)[A-Za-z0-9_.-]{16,}")


class BenchmarkExportError(RuntimeError):
    """A safe export-integrity or lifecycle failure."""


@dataclass(frozen=True, slots=True)
class BenchmarkArtifacts:
    document: dict[str, Any]
    results_bytes: bytes
    results_sha256: str
    candidates: dict[str, str]


class BenchmarkExporter:
    def __init__(
        self,
        repository: BenchmarkRepository,
        evaluations: EvaluationRepository,
        datasets: BenchmarkDatasetRegistry,
    ) -> None:
        self._repository = repository
        self._evaluations = evaluations
        self._datasets = datasets

    async def build(
        self,
        run_id: UUID,
        *,
        allow_incomplete: bool = False,
        secret_values: tuple[str, ...] | None = None,
    ) -> BenchmarkArtifacts:
        run = await self._repository.get_run(run_id)
        if run is None:
            raise BenchmarkExportError(f"Unknown benchmark run: {run_id}")
        if run.status not in _TERMINAL_RUNS and not allow_incomplete:
            raise BenchmarkExportError(
                "Benchmark is not terminal; use --allow-incomplete for a clearly marked export."
            )
        dataset = self._datasets.get(run.dataset_id, run.dataset_version)
        if dataset.dataset_fingerprint != run.dataset_fingerprint:
            raise BenchmarkExportError("Stored dataset fingerprint does not match the registry.")
        rows = await self._repository.result_rows(run_id)
        snapshots: dict[UUID, EvaluationSnapshot] = {}
        candidates: dict[str, str] = {}
        sample_documents: list[dict[str, Any]] = []
        for row in rows:
            snapshot = await self._snapshot(row)
            if snapshot is not None:
                snapshots[row.sample.evaluation_id] = snapshot
            candidate_path = self._verified_candidate(row, snapshot, candidates)
            sample_documents.append(_sample_document(row, snapshot, candidate_path))
        metric_rows = [
            _row_with_snapshot_metrics(row, snapshots.get(row.sample.evaluation_id)) for row in rows
        ]
        meaningful_results = bool(snapshots)
        ai_enabled = _ai_enabled(snapshots.values())
        leaderboard = (
            []
            if run.status is BenchmarkRunStatus.FAILED or not meaningful_results
            else build_leaderboard(run.model_configs, metric_rows)
        )
        document: dict[str, Any] = {
            "schema_version": "2",
            "run": {
                "benchmark_run_id": run.benchmark_run_id,
                "status": run.status,
                "incomplete": run.status not in _TERMINAL_RUNS,
                "meaningful_results": meaningful_results,
                "created_at": run.created_at,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "samples_per_task": run.samples_per_task,
                "planned_sample_count": run.planned_sample_count,
                "benchmark_run_fingerprint": run.benchmark_run_fingerprint,
            },
            "dataset": {
                "id": dataset.dataset_id,
                "version": dataset.dataset_version,
                "fingerprint": dataset.dataset_fingerprint,
                "title": dataset.title,
                "description": dataset.description,
                "tasks": [entry.model_dump(mode="json") for entry in dataset.task_entries],
            },
            "benchmark_policy": {"version": run.benchmark_policy_version},
            "coding_prompt": {
                "version": run.coding_prompt_version,
                "hash": run.coding_prompt_hash,
            },
            "evaluator": {
                "fingerprint": run.evaluator_fingerprint,
                "identities": _evaluation_identities(snapshots.values()),
                "ai_policies": _ai_policies(snapshots.values()),
                "ai_enabled": ai_enabled,
                "ai_cost": {
                    "actual_cost": None,
                    "currency": None,
                    "status": (
                        "unknown_no_pricing_snapshot"
                        if ai_enabled
                        else "not_applicable"
                        if ai_enabled is False
                        else "unknown"
                    ),
                },
            },
            "metric_semantics": {
                "weighted_mean_score": (
                    "Task-weighted mean deterministic score over completed evaluations only; "
                    "this remains the primary ranking metric."
                ),
                "coverage": "Completed evaluations divided by all planned samples.",
                "successful_generation_rate": (
                    "Samples with persisted generation artifacts divided by all planned samples."
                ),
                "evaluation_completion_rate": (
                    "Completed evaluations divided by successful generations."
                ),
                "correctness_pass_rate": (
                    "Completed evaluations with zero failed official correctness tests divided "
                    "by completed evaluations."
                ),
                "end_to_end_success_rate": (
                    "Samples with a persisted generation, completed evaluation, and zero failed "
                    "official correctness tests divided by all planned samples."
                ),
                "perfect_deterministic_score_rate": (
                    "Completed evaluations with total deterministic score exactly 100 divided "
                    "by completed evaluations."
                ),
                "coverage_adjusted_deterministic_score": (
                    "Supplemental task-weighted mean over all planned samples, assigning zero "
                    "only to missing evaluations. Primary ranking is unchanged."
                ),
                "generation_latency": "Provider generation request latency.",
                "test_execution_duration": (
                    "Authoritative sandbox correctness-test duration from evaluation.tests."
                ),
                "evaluation_lifecycle_duration": (
                    "Wall-clock time from benchmark sample creation to evaluation snapshot "
                    "completion; includes queueing and generation."
                ),
            },
            "models": [_model_document(config, metric_rows) for config in run.model_configs],
            "samples": sample_documents,
            "per_task": _per_task_documents(leaderboard),
            "leaderboard": [entry.model_dump(mode="json") for entry in leaderboard],
            "failures": _failure_documents(rows),
            "totals": _totals(metric_rows),
            "disclaimer": (
                "These results apply to the exact dataset, prompts, parameters, provider "
                "backends, evaluator configuration, and sample count recorded in this benchmark "
                "run. They are not a universal ranking of model intelligence."
            ),
        }
        if (
            run.coding_prompt_version != CODING_PROMPT_VERSION
            or run.coding_prompt_hash != CODING_PROMPT_HASH
        ):
            # Historical identities stay exportable without accidental reinterpretation.
            document["coding_prompt"]["current_runtime_match"] = False
        results_bytes = canonical_json_bytes(document)
        values = secret_values if secret_values is not None else _environment_secret_values()
        _ensure_secret_free(results_bytes.decode("utf-8"), candidates, values)
        return BenchmarkArtifacts(
            document=document,
            results_bytes=results_bytes,
            results_sha256=hashlib.sha256(results_bytes).hexdigest(),
            candidates=candidates,
        )

    async def _snapshot(self, row: BenchmarkResultRow) -> EvaluationSnapshot | None:
        if row.sample.status is not BenchmarkSampleStatus.COMPLETED:
            return None
        snapshot = await self._evaluations.get(row.sample.evaluation_id)
        if snapshot is None:
            raise BenchmarkExportError(
                f"Completed sample is missing evaluation snapshot: {row.sample.benchmark_sample_id}"
            )
        if (
            snapshot.task_id != row.sample.task_id
            or snapshot.task_version != row.sample.task_version
            or snapshot.task_fingerprint != row.sample.task_fingerprint
            or snapshot.tests_fingerprint != row.sample.tests_fingerprint
        ):
            raise BenchmarkExportError(
                f"Evaluation task identity differs from sample: {row.sample.benchmark_sample_id}"
            )
        return snapshot

    def _verified_candidate(
        self,
        row: BenchmarkResultRow,
        snapshot: EvaluationSnapshot | None,
        candidates: dict[str, str],
    ) -> str | None:
        artifact = row.artifact
        if artifact is None:
            return None
        source_hash, source_size = source_identity(artifact.source)
        if source_hash != artifact.source_hash or source_size != artifact.source_size:
            raise BenchmarkExportError(
                f"Generated source integrity check failed: {row.sample.benchmark_sample_id}"
            )
        if snapshot is not None and (
            snapshot.source_hash != artifact.source_hash or snapshot.source_text != artifact.source
        ):
            raise BenchmarkExportError(
                "Evaluation source differs from generated artifact: "
                f"{row.sample.benchmark_sample_id}"
            )
        filename = f"{row.sample.benchmark_sample_id}.py"
        candidates[filename] = artifact.source
        return f"candidates/{filename}"


def canonical_json_bytes(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            document,
            default=_json_default,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def write_export(artifacts: BenchmarkArtifacts, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    candidate_directory = output.parent / "candidates"
    candidate_directory.mkdir(parents=True, exist_ok=True)
    for filename, source in artifacts.candidates.items():
        (candidate_directory / filename).write_text(source, encoding="utf-8", newline="")
    output.write_bytes(artifacts.results_bytes)


def render_report(artifacts: BenchmarkArtifacts) -> str:
    document = artifacts.document
    run = document["run"]
    dataset = document["dataset"]
    models = document["models"]
    leaderboard = document["leaderboard"]
    status = str(run["status"])
    qualifier = "" if status == "completed" else f" — {status.upper()}"
    lines = [
        f"# CodeJudge Benchmark Report{qualifier}",
        "",
        document["disclaimer"],
        "",
        "## Run Summary",
        "",
        f"- Run ID: `{run['benchmark_run_id']}`",
        f"- Status: `{status}`",
        f"- Dataset: `{dataset['id']}@{dataset['version']}`",
        f"- Planned samples: {run['planned_sample_count']}",
        f"- Recorded samples: {len(document['samples'])}",
        f"- Results JSON SHA-256: `{artifacts.results_sha256}`",
        "",
        "## Benchmark Configuration",
        "",
        f"- Samples per task: {run['samples_per_task']}",
        f"- Models: {len(models)}",
        f"- AI evaluation: {_ai_label(document['evaluator']['ai_enabled'])}",
        "",
    ]
    if status == "failed" or not run["meaningful_results"]:
        lines.extend(
            [
                "## Leaderboard",
                "",
                "No leaderboard is shown because the run has no completed measured evaluation.",
                "",
            ]
        )
    else:
        lines.extend(_leaderboard_section(leaderboard, models))
        lines.extend(_per_task_section(document["per_task"]))
    lines.extend(_reliability_section(models))
    lines.extend(_cost_section(models, document["evaluator"]))
    lines.extend(_latency_section(models))
    lines.extend(_failure_section(document["failures"]))
    lines.extend(_provenance_section(document, artifacts.results_sha256))
    lines.extend(
        [
            "## Limitations",
            "",
            "Results are sample-count dependent and model backends may change behind a stable "
            "model name. Unknown token usage or pricing remains unknown, never zero. AI scores, "
            "when present, are "
            "supplemental and do not affect the primary ranking.",
            "",
        ]
    )
    return "\n".join(lines)


def _sample_document(
    row: BenchmarkResultRow,
    snapshot: EvaluationSnapshot | None,
    candidate_path: str | None,
) -> dict[str, Any]:
    artifact = row.artifact
    return {
        "benchmark_sample_id": row.sample.benchmark_sample_id,
        "model_config_id": row.sample.model_config_id,
        "provider_id": row.config.provider_id,
        "model": row.config.model,
        "task_id": row.sample.task_id,
        "task_version": row.sample.task_version,
        "task_fingerprint": row.sample.task_fingerprint,
        "tests_fingerprint": row.sample.tests_fingerprint,
        "sample_index": row.sample.sample_index,
        "status": row.sample.status,
        "failure_code": row.sample.failure_code,
        "generation": (
            None
            if artifact is None
            else {
                "candidate_path": candidate_path,
                "source_hash": artifact.source_hash,
                "source_size": artifact.source_size,
                "attempts": artifact.generation_attempts,
                "latency_ms": artifact.generation_latency_ms,
                "input_tokens": artifact.input_tokens,
                "output_tokens": artifact.output_tokens,
                "pricing_version": artifact.pricing_version,
                "actual_cost": artifact.generation_cost,
                "currency": artifact.currency,
            }
        ),
        "evaluation": (
            None
            if snapshot is None
            else {
                "evaluation_id": snapshot.evaluation_id,
                "created_at": snapshot.created_at,
                "completed_at": snapshot.completed_at,
                "status": snapshot.status,
                "deterministic_score": snapshot.final_score,
                "score_breakdown": snapshot.score_breakdown.model_dump(mode="json"),
                "tests": snapshot.tests.model_dump(mode="json"),
                "oom_killed": snapshot.oom_killed,
                "ai_assessment": _ai_assessment_document(snapshot),
                "test_execution_seconds": snapshot.tests.duration_seconds,
                "evaluation_lifecycle_seconds": snapshot.duration_seconds,
                "benchmark_worker_total_duration_seconds": row.sample.total_duration_seconds,
                "reproducibility_fingerprint": snapshot.reproducibility_fingerprint,
                "execution": snapshot.execution.model_dump(mode="json"),
                "codejudge_version": snapshot.codejudge_version,
                "scoring_policy_version": snapshot.scoring_policy_version,
                "analyzer_versions": dict(sorted(snapshot.analyzer_versions.items())),
            }
        ),
    }


def _ai_assessment_document(snapshot: EvaluationSnapshot) -> dict[str, Any] | None:
    assessment = snapshot.ai_assessment
    if assessment is None:
        return None
    return {
        "status": assessment.status,
        "reason": assessment.reason,
        "ai_score": assessment.ai_score,
        "judge_score": assessment.judge_score,
        "judge_disputed": assessment.judge_disputed,
        "judge_disagreement_spread": assessment.judge_disagreement_spread,
        "adversarial_robustness": (
            None
            if assessment.adversarial_tests is None
            else assessment.adversarial_tests.robustness_score
        ),
        "ai_reproducibility_fingerprint": assessment.ai_reproducibility_fingerprint,
        "provenance": assessment.provenance.model_dump(mode="json"),
    }


def _model_document(config: Any, rows: list[BenchmarkResultRow]) -> dict[str, Any]:
    selected = [row for row in rows if row.config.model_config_id == config.model_config_id]
    generated = [row for row in selected if row.artifact is not None]
    evaluated = [row for row in selected if row.deterministic_score is not None]
    correct = [row for row in evaluated if row.tests_failed == 0]
    end_to_end_successes = [row for row in correct if row.artifact is not None]
    failures: dict[str, int] = {}
    for row in selected:
        if row.sample.failure_code:
            failures[row.sample.failure_code] = failures.get(row.sample.failure_code, 0) + 1
    tokens = {
        "input": _known_sum([row.artifact.input_tokens for row in generated if row.artifact]),
        "output": _known_sum([row.artifact.output_tokens for row in generated if row.artifact]),
        "samples_with_usage": sum(
            row.artifact is not None
            and row.artifact.input_tokens is not None
            and row.artifact.output_tokens is not None
            for row in selected
        ),
    }
    costs: dict[str, Decimal] = {}
    samples_with_cost = 0
    for row in generated:
        artifact = row.artifact
        if artifact is not None and artifact.generation_cost is not None and artifact.currency:
            costs[artifact.currency] = (
                costs.get(artifact.currency, Decimal()) + artifact.generation_cost
            )
            samples_with_cost += 1
    latencies = [
        row.artifact.generation_latency_ms for row in generated if row.artifact is not None
    ]
    test_execution_durations = [
        row.test_execution_seconds for row in selected if row.test_execution_seconds is not None
    ]
    evaluation_lifecycle_durations = [
        row.evaluation_lifecycle_seconds
        for row in selected
        if row.evaluation_lifecycle_seconds is not None
    ]
    complete_cost_coverage = samples_with_cost == len(generated) and bool(generated)
    cost_per_successful_generation, generation_cost_status = _per_unit_cost(
        costs,
        denominator=len(generated),
        complete_coverage=complete_cost_coverage,
        empty_status="not_applicable_no_successful_generation",
    )
    cost_per_correct_evaluation, correct_cost_status = _per_unit_cost(
        costs,
        denominator=len(correct),
        complete_coverage=complete_cost_coverage,
        empty_status="not_applicable_no_correct_evaluation",
    )
    pricing = None if config.pricing is None else config.pricing.model_dump(mode="json")
    return {
        "model_config_id": config.model_config_id,
        "provider_id": config.provider_id,
        "model": config.model,
        "display_name": config.display_name,
        "model_configuration_fingerprint": config.model_configuration_fingerprint,
        "generation_parameters": {
            "temperature": config.temperature,
            "top_p": config.top_p,
            "max_output_tokens": config.max_output_tokens,
            "seed": config.seed,
            "output_mode": config.output_mode,
            "request_timeout_seconds": config.request_timeout_seconds,
            "max_concurrent_requests": config.max_concurrent_requests,
        },
        "pricing_snapshot": pricing,
        "planned_samples": len(selected),
        "successful_generations": len(generated),
        "completed_evaluations": len(evaluated),
        "correct_evaluations": len(correct),
        "end_to_end_successful_samples": len(end_to_end_successes),
        "generation_failures": sum(
            row.sample.status is BenchmarkSampleStatus.GENERATION_FAILED for row in selected
        ),
        "evaluation_failures": sum(
            row.sample.status is BenchmarkSampleStatus.EVALUATION_FAILED for row in selected
        ),
        "failure_codes": dict(sorted(failures.items())),
        "token_usage": tokens,
        "actual_generation_costs": dict(sorted(costs.items())),
        "samples_with_cost": samples_with_cost,
        "cost_per_successful_generation": cost_per_successful_generation,
        "cost_per_successful_generation_status": generation_cost_status,
        "cost_per_correct_evaluation": cost_per_correct_evaluation,
        "cost_per_correct_evaluation_status": correct_cost_status,
        "mean_generation_latency_ms": _mean(latencies),
        "median_generation_latency_ms": _median(latencies),
        "p95_generation_latency_ms": _p95(latencies),
        "mean_test_execution_seconds": _mean(test_execution_durations),
        "median_test_execution_seconds": _median(test_execution_durations),
        "p95_test_execution_seconds": _p95(test_execution_durations),
        "mean_evaluation_lifecycle_seconds": _mean(evaluation_lifecycle_durations),
    }


def _row_with_snapshot_metrics(
    row: BenchmarkResultRow, snapshot: EvaluationSnapshot | None
) -> BenchmarkResultRow:
    if snapshot is None:
        return row
    return replace(
        row,
        tests_passed=snapshot.tests.passed,
        tests_failed=snapshot.tests.failed,
        test_execution_seconds=snapshot.tests.duration_seconds,
        evaluation_lifecycle_seconds=snapshot.duration_seconds,
    )


def _evaluation_identities(snapshots: Any) -> list[dict[str, Any]]:
    identities: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        identity = {
            "codejudge_version": snapshot.codejudge_version,
            "scoring_policy_version": snapshot.scoring_policy_version,
            "analyzer_versions": dict(sorted(snapshot.analyzer_versions.items())),
            "execution": snapshot.execution.model_dump(mode="json"),
        }
        key = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
        identities[key] = {"identity_hash": key, **identity}
    return [identities[key] for key in sorted(identities)]


def _ai_policies(snapshots: Any) -> list[dict[str, Any]]:
    policies: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        if snapshot.ai_assessment is None:
            continue
        policy = snapshot.ai_assessment.provenance.model_dump(mode="json")
        key = hashlib.sha256(canonical_json_bytes(policy)).hexdigest()
        policies[key] = {"identity_hash": key, **policy}
    return [policies[key] for key in sorted(policies)]


def _ai_enabled(snapshots: Any) -> bool | None:
    recorded = list(snapshots)
    if not recorded:
        return None
    assessments = [
        snapshot.ai_assessment for snapshot in recorded if snapshot.ai_assessment is not None
    ]
    if not assessments:
        return False
    return any(assessment.status is not AIStatus.DISABLED for assessment in assessments)


def _per_task_documents(leaderboard: list[Any]) -> list[dict[str, Any]]:
    documents = []
    for entry in leaderboard:
        for task in entry.per_task:
            documents.append(
                {
                    "task_id": task.task_id,
                    "provider_id": entry.provider_id,
                    "model": entry.model,
                    "model_configuration_fingerprint": entry.model_configuration_fingerprint,
                    "samples": task.sample_count,
                    "mean_deterministic_score": task.scores.mean,
                    "coverage_adjusted_deterministic_score": (
                        task.coverage_adjusted_deterministic_score
                    ),
                    "correctness_pass_rate": task.correctness_pass_rate,
                    "end_to_end_success_rate": task.end_to_end_success_rate,
                    "perfect_deterministic_score_rate": (task.perfect_deterministic_score_rate),
                    "generation_failures": task.generation_failures,
                    "evaluation_failures": task.evaluation_failures,
                }
            )
    return sorted(
        documents, key=lambda item: (item["task_id"], item["model_configuration_fingerprint"])
    )


def _failure_documents(rows: list[BenchmarkResultRow]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str, str], int] = {}
    for row in rows:
        if row.sample.failure_code is None:
            continue
        key = (row.config.provider_id, row.config.model, row.sample.failure_code)
        counts[key] = counts.get(key, 0) + 1
    return [
        {"provider_id": key[0], "model": key[1], "failure_code": key[2], "count": count}
        for key, count in sorted(counts.items())
    ]


def _totals(rows: list[BenchmarkResultRow]) -> dict[str, Any]:
    return {
        "recorded_samples": len(rows),
        "completed_samples": sum(
            row.sample.status is BenchmarkSampleStatus.COMPLETED for row in rows
        ),
        "generation_failures": sum(
            row.sample.status is BenchmarkSampleStatus.GENERATION_FAILED for row in rows
        ),
        "evaluation_failures": sum(
            row.sample.status is BenchmarkSampleStatus.EVALUATION_FAILED for row in rows
        ),
        "provider_refusals": sum(row.sample.failure_code == "provider_refusal" for row in rows),
        "provider_timeouts": sum(row.sample.failure_code == "provider_timeout" for row in rows),
        "rate_limit_failures": sum(
            row.sample.failure_code == "provider_rate_limited" for row in rows
        ),
        "malformed_responses": sum(
            row.sample.failure_code in {"malformed_output", "malformed_provider_response"}
            for row in rows
        ),
        "provider_unavailable": sum(
            row.sample.failure_code == "provider_unavailable" for row in rows
        ),
    }


def _leaderboard_section(entries: list[dict[str, Any]], models: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Leaderboard",
        "",
        "Primary ranking uses the task-weighted deterministic mean over completed evaluations "
        "only. It measures generated-sample quality; coverage must be interpreted alongside it.",
        "",
        "| Rank | Model | Deterministic mean | Coverage | Coverage-adjusted score | "
        "Correctness pass | End-to-end success |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for entry in entries:
        lines.append(
            (
                "| {rank} | {model} | {mean} | {coverage} | {adjusted} | {correctness} | "
                "{end_to_end} |"
            ).format(
                rank=entry["rank"],
                model=_cell(entry["display_name"]),
                mean=_number(entry["weighted_mean_score"]),
                coverage=_percent(entry["coverage"]),
                adjusted=_number(entry["coverage_adjusted_deterministic_score"]),
                correctness=_percent(entry["correctness_pass_rate"]),
                end_to_end=_percent(entry["end_to_end_success_rate"]),
            )
        )
    lines.extend(
        [
            "",
            "Coverage-adjusted deterministic score is supplemental and assigns zero to missing "
            "planned evaluations. It does not replace the primary ranking.",
            "",
            "### Supplemental metrics",
            "",
            "| Model | Deterministic median | Perfect deterministic score | AI score | "
            "AI coverage | Generation cost |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    models_by_id = {str(model["model_config_id"]): model for model in models}
    for entry in entries:
        model = models_by_id[str(entry["model_config_id"])]
        generated = model["successful_generations"]
        cost_coverage = model["samples_with_cost"] / generated if generated else 0
        costs = _costs(entry["generation_costs"], cost_coverage)
        lines.append(
            f"| {_cell(entry['display_name'])} | "
            f"{_number(entry['deterministic_scores']['median'])} | "
            f"{_percent(entry['perfect_deterministic_score_rate'])} | "
            f"{_number(entry['mean_ai_score'])} | "
            f"{_percent(entry['ai_coverage'])} | {costs} |"
        )
    return [*lines, ""]


def _per_task_section(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Per-Task Results",
        "",
        "| Task | Model | Samples | Deterministic mean | Coverage-adjusted score | "
        "Correctness pass | End-to-end success | Perfect deterministic score |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {_cell(row['task_id'])} | {_cell(row['model'])} | {row['samples']} | "
            f"{_number(row['mean_deterministic_score'])} | "
            f"{_number(row['coverage_adjusted_deterministic_score'])} | "
            f"{_percent(row['correctness_pass_rate'])} | "
            f"{_percent(row['end_to_end_success_rate'])} | "
            f"{_percent(row['perfect_deterministic_score_rate'])} |"
        )
    return [*lines, ""]


def _reliability_section(models: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Reliability / Coverage",
        "",
        "Successful generation uses planned samples as its denominator; evaluation completion "
        "uses successful generations; correctness pass uses completed evaluations; end-to-end "
        "success uses planned samples.",
        "",
        "| Model | Successful generation | Evaluation completion | Correctness pass | "
        "End-to-end success | Generation failures | Refusals | Timeouts | Rate limits | "
        "Malformed | Provider unavailable |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model in models:
        planned = model["planned_samples"]
        generated = model["successful_generations"]
        completed = model["completed_evaluations"]
        failures = model["failure_codes"]
        malformed = failures.get("malformed_output", 0) + failures.get(
            "malformed_provider_response", 0
        )
        lines.append(
            f"| {_cell(model['display_name'])} | "
            f"{_percent(generated / planned if planned else 0)} | "
            f"{_percent(completed / generated if generated else 0)} | "
            f"{_percent(model['correct_evaluations'] / completed if completed else None)} | "
            f"{_percent(model['end_to_end_successful_samples'] / planned if planned else 0)} | "
            f"{model['generation_failures']} | "
            f"{failures.get('provider_refusal', 0)} | {failures.get('provider_timeout', 0)} | "
            f"{failures.get('provider_rate_limited', 0)} | "
            f"{malformed} | {failures.get('provider_unavailable', 0)} |"
        )
    return [*lines, ""]


def _cost_section(models: list[dict[str, Any]], evaluator: dict[str, Any]) -> list[str]:
    lines = [
        "## Cost",
        "",
        "All values below are actual recorded usage, not preflight estimates.",
        "",
        "| Model | Input tokens | Output tokens | Generation cost | Cost coverage | "
        "Cost / successful generation | Cost / correct evaluation |",
        "| --- | ---: | ---: | --- | ---: | --- | --- |",
    ]
    for model in models:
        tokens = model["token_usage"]
        generated = model["successful_generations"]
        costs = model["actual_generation_costs"]
        cost_per_generation = _cost_metric(
            model["cost_per_successful_generation"],
            model["cost_per_successful_generation_status"],
        )
        cost_per_correct = _cost_metric(
            model["cost_per_correct_evaluation"],
            model["cost_per_correct_evaluation_status"],
        )
        lines.append(
            f"| {_cell(model['display_name'])} | "
            f"{_known(tokens['input'], tokens['samples_with_usage'], generated)} | "
            f"{_known(tokens['output'], tokens['samples_with_usage'], generated)} | "
            f"{_costs(costs, model['samples_with_cost'] / generated if generated else 0)} | "
            f"{_percent(model['samples_with_cost'] / generated if generated else 0)} | "
            f"{cost_per_generation} | {cost_per_correct} |"
        )
    if evaluator["ai_enabled"]:
        ai_cost = (
            "AI evaluation cost: unknown. It is separate, and Phase 7 persistence contains no "
            "evaluator-pricing snapshot."
        )
    elif evaluator["ai_enabled"] is False:
        ai_cost = "AI evaluation cost: not applicable (AI evaluation disabled)."
    else:
        ai_cost = "AI evaluation cost: unknown (no completed sample)."
    return [*lines, "", ai_cost, "It is never combined with generation cost.", ""]


def _latency_section(models: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Latency",
        "",
        "Provider generation latency, authoritative sandbox correctness-test execution, and "
        "benchmark evaluation lifecycle time are reported separately. Lifecycle time begins at "
        "sample creation and includes queueing and generation; it is not code execution time.",
        "",
        "| Model | Generation median | Generation p95 | Test execution mean | Test execution p95 | "
        "Evaluation lifecycle mean |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model in models:
        lines.append(
            f"| {_cell(model['display_name'])} | "
            f"{_milliseconds(model['median_generation_latency_ms'])} | "
            f"{_milliseconds(model['p95_generation_latency_ms'])} | "
            f"{_seconds(model['mean_test_execution_seconds'])} | "
            f"{_seconds(model['p95_test_execution_seconds'])} | "
            f"{_seconds(model['mean_evaluation_lifecycle_seconds'])} |"
        )
    return [*lines, ""]


def _failure_section(failures: list[dict[str, Any]]) -> list[str]:
    lines = ["## Failures & Refusals", ""]
    if not failures:
        return [*lines, "No terminal generation or evaluation failures were recorded.", ""]
    lines.extend(["| Provider | Model | Safe failure code | Count |", "| --- | --- | --- | ---: |"])
    for item in failures:
        lines.append(
            f"| {_cell(item['provider_id'])} | {_cell(item['model'])} | "
            f"`{_cell(item['failure_code'])}` | {item['count']} |"
        )
    return [*lines, ""]


def _provenance_section(document: dict[str, Any], results_hash: str) -> list[str]:
    run = document["run"]
    dataset = document["dataset"]
    prompt = document["coding_prompt"]
    evaluator = document["evaluator"]
    lines = [
        "## Reproducibility / Provenance",
        "",
        f"- Benchmark run fingerprint: `{run['benchmark_run_fingerprint']}`",
        f"- Results JSON SHA-256: `{results_hash}`",
        f"- Dataset fingerprint: `{dataset['fingerprint']}`",
        f"- Benchmark policy version: `{document['benchmark_policy']['version']}`",
        f"- Coding prompt version/hash: `{prompt['version']}` / `{prompt['hash']}`",
        f"- Evaluator fingerprint: `{evaluator['fingerprint']}`",
        "- Model configuration fingerprints:",
    ]
    lines.extend(
        f"  - `{model['provider_id']}/{model['model']}`: "
        f"`{model['model_configuration_fingerprint']}`; output mode "
        f"`{model['generation_parameters']['output_mode']}`; request timeout "
        f"`{model['generation_parameters']['request_timeout_seconds']}s`; provider concurrency "
        f"`{model['generation_parameters']['max_concurrent_requests'] or 'unlimited'}`"
        for model in document["models"]
    )
    output_modes = {model["generation_parameters"]["output_mode"] for model in document["models"]}
    if len(output_modes) > 1:
        lines.append(
            "- Comparability warning: this run mixes generation output modes; direct model "
            "comparisons should preferably use one shared mode."
        )
    lines.append("- Task/test fingerprints:")
    lines.extend(
        f"  - `{task['task_id']}@{task['task_version']}`: task "
        f"`{task['task_fingerprint']}`, tests `{task['tests_fingerprint']}`"
        for task in dataset["tasks"]
    )
    if evaluator["identities"]:
        lines.append("- Recorded evaluator runtime identities:")
        for identity in evaluator["identities"]:
            execution = identity["execution"]
            sandbox_identity = (
                execution.get("sandbox_image_id") or execution.get("sandbox_image") or "unknown"
            )
            lines.append(
                f"  - CodeJudge `{identity['codejudge_version']}`, scoring policy "
                f"`{identity['scoring_policy_version']}`, backend `{execution['backend']}`, "
                f"sandbox `{sandbox_identity}`, "
                f"analyzers `{json.dumps(identity['analyzer_versions'], sort_keys=True)}`"
            )
    else:
        lines.append("- Recorded evaluator runtime identities: unavailable (no completed sample)")
    if evaluator["ai_enabled"]:
        lines.append("- Supplemental AI policy identities are preserved in `results.json`.")
    elif evaluator["ai_enabled"] is False:
        lines.append("- Supplemental AI policy: disabled (identity preserved in `results.json`)")
    else:
        lines.append("- Supplemental AI policy: unknown (no completed sample)")
    return [*lines, ""]


def _ensure_secret_free(text: str, candidates: dict[str, str], values: tuple[str, ...]) -> None:
    combined = "\n".join([text, *candidates.values()])
    if _SECRET_PATTERN.search(combined):
        raise BenchmarkExportError("Export secret scan rejected an authorization token pattern.")
    for value in values:
        if len(value) >= 8 and value in combined:
            raise BenchmarkExportError("Export secret scan found a configured secret value.")


def _environment_secret_values() -> tuple[str, ...]:
    sensitive_fragments = (
        "API_KEY",
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "DATABASE_URL",
        "REDIS_URL",
        "BASE_URL",
    )
    return tuple(
        value
        for name, value in os.environ.items()
        if value and any(fragment in name.upper() for fragment in sensitive_fragments)
    )


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _known_sum(values: list[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _per_unit_cost(
    costs: dict[str, Decimal],
    *,
    denominator: int,
    complete_coverage: bool,
    empty_status: str,
) -> tuple[dict[str, Decimal] | None, str]:
    if denominator == 0:
        return None, empty_status
    if not complete_coverage:
        return None, "unknown_incomplete_cost_coverage"
    unit = Decimal(denominator)
    precision = Decimal("0.000000000001")
    return (
        {
            currency: (amount / unit).quantize(precision)
            for currency, amount in sorted(costs.items())
        },
        "available",
    )


def _mean(values: Sequence[float | int]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: Sequence[float | int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[midpoint])
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _p95(values: Sequence[float | int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, (95 * len(ordered) + 99) // 100 - 1)
    return float(ordered[index])


def _number(value: float | int | None) -> str:
    return "unknown" if value is None else f"{value:.2f}"


def _percent(value: float | int | None) -> str:
    return "unknown" if value is None else f"{float(value) * 100:.1f}%"


def _milliseconds(value: float | int | None) -> str:
    return "unknown" if value is None else f"{float(value):.1f} ms"


def _seconds(value: float | int | None) -> str:
    return "unknown" if value is None else f"{float(value):.3f} s"


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _costs(costs: dict[str, Any], coverage: float | int) -> str:
    if not costs or float(coverage) < 1:
        return (
            "unknown"
            if not costs
            else ", ".join(f"{key} {value} (partial)" for key, value in sorted(costs.items()))
        )
    return ", ".join(f"{key} {value}" for key, value in sorted(costs.items()))


def _cost_metric(costs: dict[str, Any] | None, status: str) -> str:
    if costs is not None:
        return ", ".join(f"{key} {value}" for key, value in sorted(costs.items()))
    if status.startswith("not_applicable"):
        return "not applicable"
    return "unknown"


def _known(value: object, coverage: int, total: int) -> str:
    if value is None or coverage < total:
        return "unknown" if value is None else f"{value} (partial)"
    return str(value)


def _ai_label(value: bool | None) -> str:
    if value is None:
        return "unknown (no completed sample)"
    return "enabled" if value else "disabled"
