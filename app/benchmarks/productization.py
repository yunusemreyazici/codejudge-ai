"""Read-only benchmark browsing, deterministic comparison, and local archives."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from app.benchmarks.exporting import (
    BenchmarkArtifacts,
    canonical_json_bytes,
    render_report,
    write_export,
)
from app.benchmarks.models import BenchmarkRunStatus, BenchmarkSampleStatus
from app.benchmarks.repositories import BenchmarkRepository
from app.benchmarks.statistics import build_leaderboard
from app.core.version import codejudge_version

COMPARISON_SCHEMA_VERSION = "1"
ARCHIVE_SCHEMA_VERSION = "1"


class BenchmarkProductError(RuntimeError):
    """A safe browsing, comparison, archive, or verification failure."""


class ArchiveIntegrityError(BenchmarkProductError):
    """A local benchmark archive failed structural or cryptographic verification."""


@dataclass(frozen=True, slots=True)
class RunListRow:
    run_id: str
    status: str
    dataset: str
    models: str
    planned: int
    completed: int
    coverage: float
    created_at: datetime
    completed_at: datetime | None
    primary_winner: str | None
    best_weighted_mean: float | None
    generation_failures: int


async def build_run_listing(
    repository: BenchmarkRepository,
    *,
    limit: int,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
) -> list[RunListRow]:
    if not 1 <= limit <= 100:
        raise BenchmarkProductError("List limit must be between 1 and 100.")
    runs = await repository.list_runs(
        limit=limit,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )
    listing: list[RunListRow] = []
    for run in runs:
        rows = await repository.result_rows(run.benchmark_run_id)
        completed = sum(row.sample.status is BenchmarkSampleStatus.COMPLETED for row in rows)
        leaderboard = (
            build_leaderboard(run.model_configs, rows)
            if completed and run.status is not BenchmarkRunStatus.FAILED
            else []
        )
        winner = leaderboard[0] if leaderboard else None
        listing.append(
            RunListRow(
                run_id=str(run.benchmark_run_id),
                status=run.status.value,
                dataset=f"{run.dataset_id}@{run.dataset_version}",
                models=", ".join(config.display_name for config in run.model_configs),
                planned=run.planned_sample_count,
                completed=completed,
                coverage=(completed / run.planned_sample_count if run.planned_sample_count else 0),
                created_at=run.created_at,
                completed_at=run.completed_at,
                primary_winner=None if winner is None else winner.display_name,
                best_weighted_mean=None if winner is None else winner.weighted_mean_score,
                generation_failures=sum(
                    row.sample.status is BenchmarkSampleStatus.GENERATION_FAILED for row in rows
                ),
            )
        )
    return listing


def render_run_listing(rows: Sequence[RunListRow]) -> str:
    if not rows:
        return "No persisted benchmark runs found."
    lines = [
        "| Run ID | Status | Dataset | Models | Planned | Completed | Coverage | Created | "
        "Completed At | Primary winner | Best mean | Generation failures |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| `{row.run_id}` | {row.status} | {row.dataset} | {_cell(row.models)} | "
            f"{row.planned} | {row.completed} | {_percent(row.coverage)} | "
            f"{_timestamp(row.created_at)} | {_timestamp(row.completed_at)} | "
            f"{_cell(row.primary_winner or 'unknown')} | {_number(row.best_weighted_mean)} | "
            f"{row.generation_failures} |"
        )
    return "\n".join(lines)


def render_run_show(artifacts: BenchmarkArtifacts) -> str:
    document = artifacts.document
    run = document["run"]
    dataset = document["dataset"]
    totals = document["totals"]
    lines = [
        f"Benchmark run {run['benchmark_run_id']}",
        f"Status: {run['status']}",
        f"Dataset: {dataset['id']}@{dataset['version']}",
        f"Dataset fingerprint: {dataset['fingerprint']}",
        f"Benchmark run fingerprint: {run['benchmark_run_fingerprint']}",
        f"Created: {run['created_at']}",
        f"Completed at: {run['completed_at'] or 'unknown'}",
        f"Planned: {run['planned_sample_count']}",
        f"Completed: {totals['completed_samples']}",
        f"Coverage: {_percent(_ratio(totals['completed_samples'], run['planned_sample_count']))}",
        f"Generation failures: {totals['generation_failures']}",
        f"Evaluation failures: {totals['evaluation_failures']}",
        "Models: " + ", ".join(model["display_name"] for model in document["models"]),
        "",
        "Primary leaderboard",
        "| Rank | Model | Mean | Coverage | Adjusted | Correctness | End-to-end | Cost | "
        "Generation p50/p95 | Test mean/p95 |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    models = {str(model["model_config_id"]): model for model in document["models"]}
    for entry in document["leaderboard"]:
        model = models[str(entry["model_config_id"])]
        generation_cost = _currency_values(model["actual_generation_costs"])
        lines.append(
            f"| {entry['rank']} | {_cell(entry['display_name'])} | "
            f"{_number(entry['weighted_mean_score'])} | {_percent(entry['coverage'])} | "
            f"{_number(entry['coverage_adjusted_deterministic_score'])} | "
            f"{_percent(entry['correctness_pass_rate'])} | "
            f"{_percent(entry['end_to_end_success_rate'])} | {generation_cost} | "
            f"{_milliseconds(entry['median_generation_latency_ms'])} / "
            f"{_milliseconds(entry['p95_generation_latency_ms'])} | "
            f"{_seconds(entry['mean_test_execution_seconds'])} / "
            f"{_seconds(entry['p95_test_execution_seconds'])} |"
        )
    return "\n".join(lines)


def build_comparison(
    run_a: BenchmarkArtifacts,
    run_b: BenchmarkArtifacts,
) -> dict[str, Any]:
    document_a = run_a.document
    document_b = run_b.document
    _require_export_v2(document_a)
    _require_export_v2(document_b)
    blockers, warnings = _compatibility(document_a, document_b)
    status = (
        "incompatible" if blockers else "compatible_with_warnings" if warnings else "compatible"
    )
    comparison: dict[str, Any] = {
        "comparison_schema_version": COMPARISON_SCHEMA_VERSION,
        "run_a": _run_reference(document_a),
        "run_b": _run_reference(document_b),
        "compatibility": {
            "status": status,
            "blockers": blockers,
            "warnings": warnings,
        },
        "added_models": [],
        "removed_models": [],
        "model_deltas": [],
        "task_deltas": [],
        "configuration_differences": [],
    }
    if blockers:
        return comparison
    matches, added, removed, identity_warnings = _match_models(document_a, document_b)
    comparison["compatibility"]["warnings"].extend(identity_warnings)
    if identity_warnings and status == "compatible":
        comparison["compatibility"]["status"] = "compatible_with_warnings"
    comparison["added_models"] = [_model_reference(model) for model in added]
    comparison["removed_models"] = [_model_reference(model) for model in removed]
    leaderboard_a = _leaderboard_by_config(document_a)
    leaderboard_b = _leaderboard_by_config(document_b)
    model_deltas: list[dict[str, Any]] = []
    task_deltas: list[dict[str, Any]] = []
    configuration_differences: list[dict[str, Any]] = []
    for model_a, model_b in matches:
        entry_a = _comparison_metric_entry(leaderboard_a, model_a)
        entry_b = _comparison_metric_entry(leaderboard_b, model_b)
        identity = _model_identity_document(model_a)
        changes = _configuration_changes(model_a, model_b)
        if changes:
            configuration_differences.append({"identity": identity, "changes": changes})
            comparison["compatibility"]["warnings"].append(
                f"Model configuration changed for {identity['provider_id']}/{identity['model']}."
            )
        model_deltas.append(
            {
                "identity": identity,
                "display_name_a": model_a["display_name"],
                "display_name_b": model_b["display_name"],
                "model_configuration_fingerprint_a": model_a["model_configuration_fingerprint"],
                "model_configuration_fingerprint_b": model_b["model_configuration_fingerprint"],
                "metrics": _model_metric_deltas(entry_a, entry_b, model_a, model_b),
            }
        )
        task_deltas.extend(_task_deltas(document_a, document_b, model_a, model_b))
    comparison["model_deltas"] = model_deltas
    comparison["task_deltas"] = task_deltas
    comparison["configuration_differences"] = configuration_differences
    if comparison["compatibility"]["warnings"]:
        comparison["compatibility"]["status"] = "compatible_with_warnings"
    return comparison


def comparison_json_bytes(comparison: dict[str, Any]) -> bytes:
    return canonical_json_bytes(comparison)


def render_comparison_markdown(comparison: dict[str, Any]) -> str:
    run_a = comparison["run_a"]
    run_b = comparison["run_b"]
    compatibility = comparison["compatibility"]
    lines = [
        "# CodeJudge Benchmark Comparison",
        "",
        f"- Run A: `{run_a['run_id']}`",
        f"- Run B: `{run_b['run_id']}`",
        f"- Dataset: `{run_a['dataset_id']}@{run_a['dataset_version']}`",
        f"- Compatibility: **{compatibility['status'].replace('_', ' ')}**",
        "",
    ]
    if compatibility["blockers"]:
        lines.extend(["## Compatibility blockers", ""])
        lines.extend(f"- {_cell(reason)}" for reason in compatibility["blockers"])
        lines.extend(["", "No metric deltas are produced for incompatible runs.", ""])
        return "\n".join(lines)
    if compatibility["warnings"]:
        lines.extend(["## Compatibility warnings", ""])
        lines.extend(f"- {_cell(warning)}" for warning in compatibility["warnings"])
        lines.append("")
    lines.extend(
        [
            "## Model deltas",
            "",
            "Rate deltas are percentage points (pp).",
            "",
            "| Model | Deterministic mean | Coverage | Adjusted score | Correctness | "
            "End-to-end | Generation success | Generation failures |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for model in comparison["model_deltas"]:
        metrics = model["metrics"]
        lines.append(
            f"| {_identity_label(model['identity'])} | "
            f"{_score_transition(metrics['weighted_deterministic_mean'])} | "
            f"{_rate_transition(metrics['coverage'])} | "
            f"{_score_transition(metrics['coverage_adjusted_deterministic_score'])} | "
            f"{_rate_transition(metrics['correctness_pass_rate'])} | "
            f"{_rate_transition(metrics['end_to_end_success_rate'])} | "
            f"{_rate_transition(metrics['successful_generation_rate'])} | "
            f"{_rate_transition(metrics['generation_failure_rate'])} |"
        )
    lines.extend(
        [
            "",
            "## Latency and cost deltas",
            "",
            "| Model | Generation median | Generation p95 | Test mean | Test p95 | "
            "Generation cost | Cost / generation | Cost / correct |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for model in comparison["model_deltas"]:
        metrics = model["metrics"]
        lines.append(
            f"| {_identity_label(model['identity'])} | "
            f"{_duration_transition(metrics['generation_median_latency_ms'], 'ms')} | "
            f"{_duration_transition(metrics['generation_p95_latency_ms'], 'ms')} | "
            f"{_duration_transition(metrics['test_execution_mean_seconds'], 's')} | "
            f"{_duration_transition(metrics['test_execution_p95_seconds'], 's')} | "
            f"{_currency_transition(metrics['generation_cost'])} | "
            f"{_currency_transition(metrics['cost_per_successful_generation'])} | "
            f"{_currency_transition(metrics['cost_per_correct_evaluation'])} |"
        )
    lines.extend(["", "## Per-task differences", ""])
    lines.extend(
        [
            "| Model | Task | Sample | Run A score | Run B score | Delta | A correctness | "
            "B correctness | A generation status | B generation status |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    for task in comparison["task_deltas"]:
        lines.append(
            f"| {_identity_label(task['identity'])} | {_cell(task['task_id'])} | "
            f"{task['sample_index']} | {_number(task['score_a'])} | "
            f"{_number(task['score_b'])} | {_signed(task['score_delta'])} | "
            f"{_boolean(task['correctness_a'])} | {_boolean(task['correctness_b'])} | "
            f"{_cell(task['generation_status_a'])} | "
            f"{_cell(task['generation_status_b'])} |"
        )
    lines.extend(["", "## Added and removed models", ""])
    lines.append(
        "- Added: "
        + (", ".join(_identity_label(item) for item in comparison["added_models"]) or "none")
    )
    lines.append(
        "- Removed: "
        + (", ".join(_identity_label(item) for item in comparison["removed_models"]) or "none")
    )
    lines.extend(["", "## Configuration differences", ""])
    if not comparison["configuration_differences"]:
        lines.append("No matching model configuration changes were recorded.")
    else:
        for item in comparison["configuration_differences"]:
            lines.append(f"### {_identity_label(item['identity'])}")
            lines.append("")
            for change in item["changes"]:
                lines.append(f"- `{change['field']}`: `{change['a']}` → `{change['b']}`")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_comparison(
    comparison: dict[str, Any],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".json":
        output.write_bytes(comparison_json_bytes(comparison))
        return
    if output.suffix.lower() == ".md":
        output.write_text(render_comparison_markdown(comparison), encoding="utf-8", newline="")
        return
    raise BenchmarkProductError("Comparison output must use a .json or .md extension.")


def write_archive(
    artifacts: BenchmarkArtifacts,
    output: Path,
    *,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    if output.exists():
        if not output.is_dir():
            raise BenchmarkProductError(f"Archive output is not a directory: {output}")
        if any(output.iterdir()):
            raise BenchmarkProductError(f"Archive directory is not empty: {output}")
    actual_results_hash = _sha256(artifacts.results_bytes)
    if actual_results_hash != artifacts.results_sha256:
        raise BenchmarkProductError("Archive source results failed their SHA-256 integrity check.")
    for filename in artifacts.candidates:
        candidate_name = PurePosixPath(filename)
        if candidate_name.parts != (filename,) or candidate_name.is_absolute():
            raise BenchmarkProductError(f"Archive candidate filename is unsafe: {filename}")
    output.mkdir(parents=True, exist_ok=True)
    results_path = output / "results.json"
    write_export(artifacts, results_path)
    report_bytes = render_report(artifacts).encode("utf-8")
    (output / "report.md").write_bytes(report_bytes)
    candidate_hashes = {
        f"candidates/{filename}": _sha256(source.encode("utf-8"))
        for filename, source in sorted(artifacts.candidates.items())
    }
    document = artifacts.document
    manifest = {
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
        "created_at": (created_at or datetime.now(UTC)).isoformat(),
        "codejudge_version": codejudge_version(),
        "run_id": str(document["run"]["benchmark_run_id"]),
        "dataset_fingerprint": document["dataset"]["fingerprint"],
        "benchmark_run_fingerprint": document["run"]["benchmark_run_fingerprint"],
        "results_sha256": actual_results_hash,
        "report_sha256": _sha256(report_bytes),
        "candidate_sha256": candidate_hashes,
        "expected_files": [
            "results.json",
            "report.md",
            *candidate_hashes,
            "manifest.json",
        ],
    }
    (output / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    return manifest


def verify_archive(path: Path) -> dict[str, Any]:
    if not path.is_dir():
        raise ArchiveIntegrityError(f"Archive directory not found: {path}")
    manifest_path = path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArchiveIntegrityError(
            "Archive integrity check failed: invalid manifest.json"
        ) from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("archive_schema_version") != ARCHIVE_SCHEMA_VERSION
    ):
        raise ArchiveIntegrityError("Archive integrity check failed: unsupported manifest schema")
    candidate_hashes = manifest.get("candidate_sha256")
    expected_files = manifest.get("expected_files")
    if not isinstance(candidate_hashes, dict) or not isinstance(expected_files, list):
        raise ArchiveIntegrityError("Archive integrity check failed: incomplete manifest")
    normalized_candidates = _validated_archive_paths(list(candidate_hashes))
    if any(PurePosixPath(relative).parts[0] != "candidates" for relative in normalized_candidates):
        raise ArchiveIntegrityError(
            "Archive integrity check failed: candidate paths must be under candidates/"
        )
    normalized_expected = _validated_archive_paths(expected_files)
    required = {"manifest.json", "results.json", "report.md", *normalized_candidates}
    if normalized_expected != required:
        raise ArchiveIntegrityError("Archive integrity check failed: expected file list mismatch")
    actual_files: set[str] = set()
    for candidate in path.rglob("*"):
        if candidate.is_symlink():
            raise ArchiveIntegrityError(
                f"Archive integrity check failed: symbolic link is not allowed: "
                f"{candidate.relative_to(path).as_posix()}"
            )
        if candidate.is_file():
            actual_files.add(candidate.relative_to(path).as_posix())
    if actual_files != normalized_expected:
        missing = sorted(normalized_expected - actual_files)
        unexpected = sorted(actual_files - normalized_expected)
        detail = f"missing={missing}, unexpected={unexpected}"
        raise ArchiveIntegrityError(f"Archive integrity check failed: file set mismatch ({detail})")
    _verify_file_hash(path, "results.json", manifest.get("results_sha256"))
    _verify_file_hash(path, "report.md", manifest.get("report_sha256"))
    for relative, expected_hash in sorted(candidate_hashes.items()):
        _verify_file_hash(path, relative, expected_hash)
    try:
        results = json.loads((path / "results.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArchiveIntegrityError(
            "Archive integrity check failed: invalid results.json"
        ) from error
    if not isinstance(results, dict):
        raise ArchiveIntegrityError("Archive integrity check failed: results.json is not an object")
    if str(results.get("schema_version")) != "2":
        raise ArchiveIntegrityError(
            "Archive integrity check failed: results schema is not version 2"
        )
    if str(results.get("run", {}).get("benchmark_run_id")) != str(manifest.get("run_id")):
        raise ArchiveIntegrityError("Archive integrity check failed: run ID mismatch")
    if results.get("dataset", {}).get("fingerprint") != manifest.get("dataset_fingerprint"):
        raise ArchiveIntegrityError("Archive integrity check failed: dataset fingerprint mismatch")
    if results.get("run", {}).get("benchmark_run_fingerprint") != manifest.get(
        "benchmark_run_fingerprint"
    ):
        raise ArchiveIntegrityError("Archive integrity check failed: run fingerprint mismatch")
    _verify_candidate_sources(path, results, candidate_hashes)
    return manifest


def parse_dataset_selector(selector: str | None) -> tuple[str | None, str | None]:
    if selector is None:
        return None, None
    dataset_id, separator, dataset_version = selector.partition("@")
    if not separator or not dataset_id or not dataset_version or "@" in dataset_version:
        raise BenchmarkProductError("Dataset filter must use the form <dataset-id>@<version>.")
    return dataset_id, dataset_version


def _compatibility(
    document_a: Mapping[str, Any], document_b: Mapping[str, Any]
) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    exact_fields = [
        ("dataset id", document_a["dataset"]["id"], document_b["dataset"]["id"]),
        ("dataset version", document_a["dataset"]["version"], document_b["dataset"]["version"]),
        (
            "dataset fingerprint",
            document_a["dataset"]["fingerprint"],
            document_b["dataset"]["fingerprint"],
        ),
        (
            "task/test fingerprints",
            _task_identities(document_a),
            _task_identities(document_b),
        ),
        (
            "benchmark policy version",
            document_a["benchmark_policy"]["version"],
            document_b["benchmark_policy"]["version"],
        ),
        (
            "coding prompt version",
            document_a["coding_prompt"]["version"],
            document_b["coding_prompt"]["version"],
        ),
        (
            "coding prompt hash",
            document_a["coding_prompt"]["hash"],
            document_b["coding_prompt"]["hash"],
        ),
        (
            "scoring policy",
            _scoring_policies(document_a),
            _scoring_policies(document_b),
        ),
        ("evaluator semantics", _evaluator_semantics(document_a), _evaluator_semantics(document_b)),
    ]
    for label, value_a, value_b in exact_fields:
        if value_a != value_b:
            blockers.append(f"Runs use incompatible {label}.")
    if document_a["run"]["samples_per_task"] != document_b["run"]["samples_per_task"]:
        warnings.append("Runs use different samples-per-task counts.")
    if document_a["evaluator"]["fingerprint"] != document_b["evaluator"][
        "fingerprint"
    ] and _evaluator_semantics(document_a) == _evaluator_semantics(document_b):
        warnings.append(
            "Evaluator fingerprints differ, but the exported evaluator semantics are equivalent."
        )
    return blockers, warnings


def _match_models(
    document_a: Mapping[str, Any], document_b: Mapping[str, Any]
) -> tuple[
    list[tuple[dict[str, Any], dict[str, Any]]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
]:
    groups_a = _model_groups(document_a["models"])
    groups_b = _model_groups(document_b["models"])
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    warnings: list[str] = []
    for identity in sorted(set(groups_a) | set(groups_b)):
        models_a = groups_a.get(identity, [])
        models_b = groups_b.get(identity, [])
        if len(models_a) == 1 and len(models_b) == 1:
            matches.append((models_a[0], models_b[0]))
            continue
        remaining_a = list(models_a)
        remaining_b = list(models_b)
        for model_a in list(remaining_a):
            exact = next(
                (
                    model_b
                    for model_b in remaining_b
                    if model_b["model_configuration_fingerprint"]
                    == model_a["model_configuration_fingerprint"]
                ),
                None,
            )
            if exact is not None:
                matches.append((model_a, exact))
                remaining_a.remove(model_a)
                remaining_b.remove(exact)
        if remaining_a and remaining_b:
            warnings.append(
                f"Repeated model identity {identity[0]}/{identity[1]} is ambiguous; "
                "only equal configuration fingerprints were matched."
            )
        removed.extend(remaining_a)
        added.extend(remaining_b)
    matches.sort(key=lambda pair: _model_identity(pair[0]))
    added.sort(key=_model_sort_key)
    removed.sort(key=_model_sort_key)
    return matches, added, removed, warnings


def _model_metric_deltas(
    entry_a: Mapping[str, Any],
    entry_b: Mapping[str, Any],
    model_a: Mapping[str, Any],
    model_b: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "weighted_deterministic_mean": _numeric_delta(
            entry_a["weighted_mean_score"], entry_b["weighted_mean_score"]
        ),
        "coverage": _rate_delta(entry_a["coverage"], entry_b["coverage"]),
        "coverage_adjusted_deterministic_score": _numeric_delta(
            entry_a["coverage_adjusted_deterministic_score"],
            entry_b["coverage_adjusted_deterministic_score"],
        ),
        "correctness_pass_rate": _rate_delta(
            entry_a["correctness_pass_rate"], entry_b["correctness_pass_rate"]
        ),
        "end_to_end_success_rate": _rate_delta(
            entry_a["end_to_end_success_rate"], entry_b["end_to_end_success_rate"]
        ),
        "successful_generation_rate": _rate_delta(
            entry_a["successful_generation_rate"], entry_b["successful_generation_rate"]
        ),
        "generation_failure_rate": _rate_delta(
            entry_a["generation_failure_rate"], entry_b["generation_failure_rate"]
        ),
        "generation_median_latency_ms": _numeric_delta(
            entry_a["median_generation_latency_ms"],
            entry_b["median_generation_latency_ms"],
        ),
        "generation_p95_latency_ms": _numeric_delta(
            entry_a["p95_generation_latency_ms"], entry_b["p95_generation_latency_ms"]
        ),
        "test_execution_mean_seconds": _numeric_delta(
            entry_a["mean_test_execution_seconds"], entry_b["mean_test_execution_seconds"]
        ),
        "test_execution_p95_seconds": _numeric_delta(
            entry_a["p95_test_execution_seconds"], entry_b["p95_test_execution_seconds"]
        ),
        "generation_cost": _currency_delta(
            model_a["actual_generation_costs"], model_b["actual_generation_costs"]
        ),
        "cost_per_successful_generation": _currency_delta(
            model_a["cost_per_successful_generation"],
            model_b["cost_per_successful_generation"],
        ),
        "cost_per_correct_evaluation": _currency_delta(
            model_a["cost_per_correct_evaluation"], model_b["cost_per_correct_evaluation"]
        ),
    }


def _task_deltas(
    document_a: Mapping[str, Any],
    document_b: Mapping[str, Any],
    model_a: Mapping[str, Any],
    model_b: Mapping[str, Any],
) -> list[dict[str, Any]]:
    samples_a = _samples_for_model(document_a, str(model_a["model_config_id"]))
    samples_b = _samples_for_model(document_b, str(model_b["model_config_id"]))
    rows: list[dict[str, Any]] = []
    identity = _model_identity_document(model_a)
    for key in sorted(set(samples_a) | set(samples_b)):
        sample_a = samples_a.get(key)
        sample_b = samples_b.get(key)
        score_a = _sample_score(sample_a)
        score_b = _sample_score(sample_b)
        rows.append(
            {
                "identity": identity,
                "task_id": key[0],
                "sample_index": key[1],
                "score_a": score_a,
                "score_b": score_b,
                "score_delta": (None if score_a is None or score_b is None else score_b - score_a),
                "correctness_a": _sample_correctness(sample_a),
                "correctness_b": _sample_correctness(sample_b),
                "generation_status_a": _sample_outcome(sample_a),
                "generation_status_b": _sample_outcome(sample_b),
            }
        )
    return rows


def _configuration_changes(
    model_a: Mapping[str, Any], model_b: Mapping[str, Any]
) -> list[dict[str, Any]]:
    parameters_a = model_a["generation_parameters"]
    parameters_b = model_b["generation_parameters"]
    pricing_a = model_a.get("pricing_snapshot") or {}
    pricing_b = model_b.get("pricing_snapshot") or {}
    fields = {
        "provider_id": (model_a["provider_id"], model_b["provider_id"]),
        "model": (model_a["model"], model_b["model"]),
        "output_mode": (parameters_a["output_mode"], parameters_b["output_mode"]),
        "request_timeout_seconds": (
            parameters_a["request_timeout_seconds"],
            parameters_b["request_timeout_seconds"],
        ),
        "max_concurrent_requests": (
            parameters_a["max_concurrent_requests"],
            parameters_b["max_concurrent_requests"],
        ),
        "temperature": (parameters_a["temperature"], parameters_b["temperature"]),
        "top_p": (parameters_a["top_p"], parameters_b["top_p"]),
        "seed": (parameters_a["seed"], parameters_b["seed"]),
        "max_output_tokens": (
            parameters_a["max_output_tokens"],
            parameters_b["max_output_tokens"],
        ),
        "pricing_version": (pricing_a.get("pricing_version"), pricing_b.get("pricing_version")),
    }
    changes = [
        {"field": field, "a": values[0], "b": values[1]}
        for field, values in fields.items()
        if values[0] != values[1]
    ]
    if model_a["model_configuration_fingerprint"] != model_b["model_configuration_fingerprint"]:
        changes.append(
            {
                "field": "model_configuration_fingerprint",
                "a": model_a["model_configuration_fingerprint"],
                "b": model_b["model_configuration_fingerprint"],
            }
        )
    return changes


def _run_reference(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(document["run"]["benchmark_run_id"]),
        "benchmark_run_fingerprint": document["run"]["benchmark_run_fingerprint"],
        "status": str(document["run"]["status"]),
        "created_at": document["run"]["created_at"],
        "completed_at": document["run"]["completed_at"],
        "dataset_id": document["dataset"]["id"],
        "dataset_version": document["dataset"]["version"],
        "dataset_fingerprint": document["dataset"]["fingerprint"],
    }


def _model_reference(model: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **_model_identity_document(model),
        "display_name": model["display_name"],
        "model_configuration_fingerprint": model["model_configuration_fingerprint"],
    }


def _model_identity_document(model: Mapping[str, Any]) -> dict[str, str]:
    return {"provider_id": str(model["provider_id"]), "model": str(model["model"])}


def _model_identity(model: Mapping[str, Any]) -> tuple[str, str]:
    return str(model["provider_id"]), str(model["model"])


def _model_sort_key(model: Mapping[str, Any]) -> tuple[str, str, str]:
    return (*_model_identity(model), str(model["model_configuration_fingerprint"]))


def _model_groups(models: Sequence[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for model in sorted(models, key=_model_sort_key):
        groups.setdefault(_model_identity(model), []).append(model)
    return groups


def _leaderboard_by_config(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(entry["model_config_id"]): entry for entry in document["leaderboard"]}


def _comparison_metric_entry(
    leaderboard: Mapping[str, dict[str, Any]], model: Mapping[str, Any]
) -> Mapping[str, Any]:
    entry = leaderboard.get(str(model["model_config_id"]))
    if entry is not None:
        return entry
    planned = int(model["planned_samples"])
    generated = int(model["successful_generations"])
    completed = int(model["completed_evaluations"])
    correct = int(model["correct_evaluations"])
    end_to_end = int(model["end_to_end_successful_samples"])
    return {
        "weighted_mean_score": None,
        "coverage": _ratio(completed, planned),
        "coverage_adjusted_deterministic_score": 0 if planned else None,
        "correctness_pass_rate": (correct / completed if completed else None),
        "end_to_end_success_rate": _ratio(end_to_end, planned),
        "successful_generation_rate": _ratio(generated, planned),
        "generation_failure_rate": _ratio(int(model["generation_failures"]), planned),
        "median_generation_latency_ms": model["median_generation_latency_ms"],
        "p95_generation_latency_ms": model["p95_generation_latency_ms"],
        "mean_test_execution_seconds": model["mean_test_execution_seconds"],
        "p95_test_execution_seconds": model["p95_test_execution_seconds"],
    }


def _samples_for_model(
    document: Mapping[str, Any], model_config_id: str
) -> dict[tuple[str, int], dict[str, Any]]:
    return {
        (str(sample["task_id"]), int(sample["sample_index"])): sample
        for sample in document["samples"]
        if str(sample["model_config_id"]) == model_config_id
    }


def _sample_score(sample: Mapping[str, Any] | None) -> float | None:
    evaluation = None if sample is None else sample.get("evaluation")
    return None if evaluation is None else float(evaluation["deterministic_score"])


def _sample_correctness(sample: Mapping[str, Any] | None) -> bool | None:
    evaluation = None if sample is None else sample.get("evaluation")
    return None if evaluation is None else int(evaluation["tests"]["failed"]) == 0


def _sample_outcome(sample: Mapping[str, Any] | None) -> str:
    if sample is None:
        return "missing"
    if sample.get("failure_code"):
        return str(sample["failure_code"])
    status = str(sample["status"])
    return "generated" if status == BenchmarkSampleStatus.COMPLETED.value else status


def _numeric_delta(value_a: Any, value_b: Any) -> dict[str, float | None]:
    a = None if value_a is None else float(value_a)
    b = None if value_b is None else float(value_b)
    return {"a": a, "b": b, "delta": None if a is None or b is None else b - a}


def _rate_delta(value_a: Any, value_b: Any) -> dict[str, float | None]:
    values = _numeric_delta(value_a, value_b)
    delta = values.pop("delta")
    return {**values, "delta_percentage_points": None if delta is None else delta * 100}


def _currency_delta(value_a: Any, value_b: Any) -> dict[str, Any]:
    currencies_a = _decimal_mapping(value_a)
    currencies_b = _decimal_mapping(value_b)
    currencies = sorted(set(currencies_a) | set(currencies_b))
    return {
        "currencies": [
            {
                "currency": currency,
                "a": currencies_a.get(currency),
                "b": currencies_b.get(currency),
                "delta": (
                    None
                    if currency not in currencies_a or currency not in currencies_b
                    else currencies_b[currency] - currencies_a[currency]
                ),
            }
            for currency in currencies
        ]
    }


def _decimal_mapping(value: Any) -> dict[str, Decimal]:
    if not isinstance(value, Mapping):
        return {}
    return {str(currency): Decimal(str(amount)) for currency, amount in value.items()}


def _task_identities(document: Mapping[str, Any]) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        sorted(
            (
                str(task["task_id"]),
                str(task["task_version"]),
                str(task["task_fingerprint"]),
                str(task["tests_fingerprint"]),
            )
            for task in document["dataset"]["tasks"]
        )
    )


def _scoring_policies(document: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(identity["scoring_policy_version"])
                for identity in document["evaluator"]["identities"]
            }
        )
    )


def _evaluator_semantics(document: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            json.dumps(
                {
                    "scoring_policy_version": identity["scoring_policy_version"],
                    "analyzer_versions": identity["analyzer_versions"],
                    "execution": identity["execution"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            for identity in document["evaluator"]["identities"]
        )
    )


def _require_export_v2(document: Mapping[str, Any]) -> None:
    if str(document.get("schema_version")) != "2":
        raise BenchmarkProductError("Phase 7.5 comparison requires export schema version 2.")


def _validated_archive_paths(values: Sequence[Any]) -> set[str]:
    paths: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ArchiveIntegrityError("Archive integrity check failed: invalid expected path")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or value != path.as_posix():
            raise ArchiveIntegrityError(
                f"Archive integrity check failed: unsafe expected path: {value}"
            )
        paths.add(value)
    return paths


def _verify_file_hash(path: Path, relative: str, expected_hash: Any) -> None:
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise ArchiveIntegrityError(f"Archive integrity check failed: invalid hash for {relative}")
    actual_hash = _sha256((path / relative).read_bytes())
    if actual_hash != expected_hash:
        raise ArchiveIntegrityError(f"Archive integrity check failed: {relative} SHA-256 mismatch")


def _verify_candidate_sources(
    path: Path,
    results: Mapping[str, Any],
    candidate_hashes: Mapping[str, Any],
) -> None:
    referenced: set[str] = set()
    samples = results.get("samples")
    if not isinstance(samples, list):
        raise ArchiveIntegrityError("Archive integrity check failed: invalid results samples")
    for sample in samples:
        if not isinstance(sample, Mapping):
            raise ArchiveIntegrityError("Archive integrity check failed: invalid result sample")
        generation = sample.get("generation")
        if generation is None:
            continue
        if not isinstance(generation, Mapping) or not isinstance(
            generation.get("candidate_path"), str
        ):
            raise ArchiveIntegrityError(
                "Archive integrity check failed: invalid candidate reference"
            )
        relative = generation["candidate_path"]
        normalized = _validated_archive_paths([relative])
        if PurePosixPath(next(iter(normalized))).parts[0] != "candidates":
            raise ArchiveIntegrityError(
                f"Archive integrity check failed: candidate path is outside candidates/: {relative}"
            )
        referenced.add(relative)
        if relative not in candidate_hashes:
            raise ArchiveIntegrityError(
                f"Archive integrity check failed: unmanifested candidate {relative}"
            )
        source_hash = generation.get("source_hash")
        if (
            not isinstance(source_hash, str)
            or _sha256((path / relative).read_bytes()) != source_hash
        ):
            raise ArchiveIntegrityError(
                f"Archive integrity check failed: {relative} source hash mismatch"
            )
    if referenced != set(candidate_hashes):
        raise ArchiveIntegrityError(
            "Archive integrity check failed: candidate references do not match manifest"
        )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0


def _timestamp(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return value.astimezone(UTC).isoformat()


def _number(value: Any) -> str:
    return "missing" if value is None else f"{float(value):.2f}"


def _signed(value: Any, *, suffix: str = "") -> str:
    return "missing" if value is None else f"{float(value):+.2f}{suffix}"


def _percent(value: Any) -> str:
    return "missing" if value is None else f"{float(value) * 100:.1f}%"


def _milliseconds(value: Any) -> str:
    return "missing" if value is None else f"{float(value):.1f} ms"


def _seconds(value: Any) -> str:
    return "missing" if value is None else f"{float(value):.3f} s"


def _boolean(value: Any) -> str:
    return "missing" if value is None else "passed" if value else "failed"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _identity_label(identity: Mapping[str, Any]) -> str:
    return _cell(f"{identity['provider_id']}/{identity['model']}")


def _score_transition(metric: Mapping[str, Any]) -> str:
    return f"{_number(metric['a'])} → {_number(metric['b'])} ({_signed(metric['delta'])})"


def _rate_transition(metric: Mapping[str, Any]) -> str:
    return (
        f"{_percent(metric['a'])} → {_percent(metric['b'])} "
        f"({_signed(metric['delta_percentage_points'], suffix=' pp')})"
    )


def _duration_transition(metric: Mapping[str, Any], unit: str) -> str:
    return (
        f"{_number(metric['a'])} {unit} → {_number(metric['b'])} {unit} "
        f"({_signed(metric['delta'], suffix=f' {unit}')})"
    )


def _currency_transition(metric: Mapping[str, Any]) -> str:
    currencies = metric["currencies"]
    if not currencies:
        return "unknown"
    return "; ".join(
        f"{item['currency']} {_number(item['a'])} → {_number(item['b'])} ({_signed(item['delta'])})"
        for item in currencies
    )


def _currency_values(values: Mapping[str, Any]) -> str:
    if not values:
        return "unknown"
    return ", ".join(f"{currency} {amount}" for currency, amount in sorted(values.items()))
