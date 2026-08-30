from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.benchmarks import cli
from app.benchmarks.exporting import BenchmarkArtifacts, canonical_json_bytes, render_report
from app.benchmarks.productization import (
    ArchiveIntegrityError,
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
from tests.benchmarks.test_phase72_reporting import _exporter, _fixture

FIXED_ARCHIVE_TIME = datetime(2026, 8, 29, 12, tzinfo=UTC)


async def _artifacts() -> BenchmarkArtifacts:
    run, rows, snapshots = _fixture()
    return await _exporter(run, rows, snapshots).build(run.benchmark_run_id, secret_values=())


def _copy_artifacts(
    artifacts: BenchmarkArtifacts,
    *,
    mutate: Any | None = None,
) -> BenchmarkArtifacts:
    document = copy.deepcopy(artifacts.document)
    document["run"]["benchmark_run_id"] = str(uuid4())
    document["run"]["benchmark_run_fingerprint"] = "9" * 64
    if mutate is not None:
        mutate(document)
    results = canonical_json_bytes(document)
    return BenchmarkArtifacts(
        document=document,
        results_bytes=results,
        results_sha256=hashlib.sha256(results).hexdigest(),
        candidates=dict(artifacts.candidates),
    )


class ListingRepository:
    def __init__(self, runs: list[Any], rows: list[Any]) -> None:
        self.runs = runs
        self.rows = rows
        self.arguments: tuple[int, str | None, str | None] | None = None

    async def list_runs(
        self,
        *,
        limit: int,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
    ) -> list[Any]:
        self.arguments = (limit, dataset_id, dataset_version)
        return self.runs[:limit]

    async def result_rows(self, _run_id: Any, **_: object) -> list[Any]:
        return self.rows


async def test_run_listing_and_show_reuse_persisted_metrics() -> None:
    run, result_rows, _ = _fixture()
    repository = ListingRepository([run], result_rows)

    rows = await build_run_listing(
        repository,  # type: ignore[arg-type]
        limit=20,
        dataset_id="codejudge-core",
        dataset_version="1",
    )
    listing = render_run_listing(rows)
    shown = render_run_show(await _artifacts())

    assert repository.arguments == (20, "codejudge-core", "1")
    assert str(run.benchmark_run_id) in listing
    assert "50.0%" in listing
    assert "Generation failures" in listing
    assert "Observed winner" in listing
    assert "Eligible winner" in listing
    assert "Primary winner" not in listing
    assert "Winner summary" in shown
    assert "Observed winner: good" in shown
    assert "Eligible winner: good" in shown
    assert "| Rank | Model | Eligible |" in shown
    assert "Primary leaderboard" in shown
    assert "Adjusted" in shown
    assert "Correctness" in shown
    assert "End-to-end" in shown
    assert "Generation p50/p95" in shown
    assert "Test mean/p95" in shown
    assert "Generation reliability" in shown
    assert "Generation failure diagnostics" in shown
    assert "Failure breakdown" in shown
    assert "provider_error=1" in shown
    assert "refusal" in shown
    assert "USD" in shown
    assert parse_dataset_selector("codejudge-core@2") == ("codejudge-core", "2")
    with pytest.raises(BenchmarkProductError, match="form"):
        parse_dataset_selector("codejudge-core")
    with pytest.raises(BenchmarkProductError, match="between 1 and 100"):
        await build_run_listing(repository, limit=0)  # type: ignore[arg-type]


async def test_compatible_comparison_has_config_task_and_percentage_point_deltas() -> None:
    run_a = await _artifacts()

    def mutate(document: dict[str, Any]) -> None:
        good = next(model for model in document["models"] if model["model"] == "good")
        good["generation_parameters"]["max_output_tokens"] = 200
        good["model_configuration_fingerprint"] = "8" * 64
        good_entry = next(
            entry
            for entry in document["leaderboard"]
            if str(entry["model_config_id"]) == str(good["model_config_id"])
        )
        good_entry["weighted_mean_score"] += 7.41
        good_entry["coverage"] = 0.5
        good_entry["coverage_adjusted_deterministic_score"] = 45.06
        good_entry["correctness_pass_rate"] = 0.5
        good_entry["end_to_end_success_rate"] = 0.5
        good_entry["successful_generation_rate"] = 0.5
        good_entry["generation_failure_rate"] = 0.5
        document["samples"] = [
            sample
            for sample in document["samples"]
            if str(sample["model_config_id"]) != str(good["model_config_id"])
        ]

    run_b = _copy_artifacts(run_a, mutate=mutate)
    comparison = build_comparison(run_a, run_b)
    good_delta = next(
        item for item in comparison["model_deltas"] if item["identity"]["model"] == "good"
    )
    missing = next(
        item for item in comparison["task_deltas"] if item["identity"]["model"] == "good"
    )
    markdown = render_comparison_markdown(comparison)

    assert comparison["comparison_schema_version"] == "1"
    assert comparison["compatibility"]["status"] == "compatible_with_warnings"
    assert good_delta["metrics"]["weighted_deterministic_mean"]["delta"] == pytest.approx(7.41)
    assert good_delta["metrics"]["coverage"]["delta_percentage_points"] == -50
    assert missing["score_b"] is None
    assert missing["generation_status_b"] == "missing"
    assert comparison["configuration_differences"][0]["changes"][-1]["field"] == (
        "model_configuration_fingerprint"
    )
    assert "-50.00 pp" in markdown
    assert "missing" in markdown
    assert "max_output_tokens" in markdown
    assert "API" not in comparison_json_bytes(comparison).decode("utf-8")


async def test_incompatible_comparison_refuses_deltas_and_added_removed_models_are_explicit() -> (
    None
):
    run_a = await _artifacts()
    incompatible = _copy_artifacts(
        run_a,
        mutate=lambda document: document["dataset"].__setitem__("fingerprint", "0" * 64),
    )
    refused = build_comparison(run_a, incompatible)

    assert refused["compatibility"]["status"] == "incompatible"
    assert "incompatible dataset fingerprint" in " ".join(refused["compatibility"]["blockers"])
    assert refused["model_deltas"] == []
    assert "No metric deltas" in render_comparison_markdown(refused)

    def replace_model(document: dict[str, Any]) -> None:
        model = next(item for item in document["models"] if item["model"] == "refusal")
        model["model"] = "replacement"

    changed = build_comparison(run_a, _copy_artifacts(run_a, mutate=replace_model))
    assert [item["model"] for item in changed["added_models"]] == ["replacement"]
    assert [item["model"] for item in changed["removed_models"]] == ["refusal"]


async def test_comparison_outputs_are_deterministic_and_extension_driven(tmp_path: Path) -> None:
    run_a = await _artifacts()
    run_b = _copy_artifacts(run_a)
    first = build_comparison(run_a, run_b)
    second = build_comparison(run_a, run_b)
    json_output = tmp_path / "comparison.json"
    markdown_output = tmp_path / "comparison.md"

    write_comparison(first, json_output)
    write_comparison(first, markdown_output)

    assert comparison_json_bytes(first) == comparison_json_bytes(second)
    assert json_output.read_bytes() == comparison_json_bytes(first)
    assert markdown_output.read_text(encoding="utf-8") == render_comparison_markdown(first)
    with pytest.raises(BenchmarkProductError, match=r"\.json or \.md"):
        write_comparison(first, tmp_path / "comparison.txt")


async def test_comparison_allows_different_sample_counts_with_exact_uncertainty_warning() -> None:
    run_a = await _artifacts()
    run_b = _copy_artifacts(
        run_a,
        mutate=lambda document: document["run"].__setitem__("samples_per_task", 3),
    )
    comparison = build_comparison(run_a, run_b)

    assert comparison["compatibility"]["status"] == "compatible_with_warnings"
    assert comparison["compatibility"]["blockers"] == []
    assert (
        "Runs use different samples-per-task; uncertainty estimates are not directly equivalent."
        in comparison["compatibility"]["warnings"]
    )


async def test_comparison_reports_generation_failure_count_and_normalized_category_changes() -> (
    None
):
    run_a = await _artifacts()

    def mutate(document: dict[str, Any]) -> None:
        refusal = next(model for model in document["models"] if model["model"] == "refusal")
        refusal["generation_reliability"]["failure_categories"] = {"rate_limited": 1}

    comparison = build_comparison(run_a, _copy_artifacts(run_a, mutate=mutate))
    refusal_delta = next(
        delta for delta in comparison["model_deltas"] if delta["identity"]["model"] == "refusal"
    )
    metrics = refusal_delta["metrics"]

    assert metrics["generation_failure_count"] == {"a": 1, "b": 1, "delta": 0}
    assert metrics["generation_failure_category_changes"] == [
        {"category": "rate_limited", "a": 0, "b": 1, "delta": 1},
        {"category": "provider_error", "a": 1, "b": 0, "delta": -1},
    ]
    markdown = render_comparison_markdown(comparison)
    assert "rate_limited: 0 → 1" in markdown
    assert "provider_error: 1 → 0" in markdown


async def test_historical_export_without_normalized_reliability_remains_readable() -> None:
    current = await _artifacts()

    def remove_additive_field(document: dict[str, Any]) -> None:
        document.pop("observed_winner", None)
        document.pop("eligible_winner", None)
        document.pop("winner_state", None)
        document.pop("winner_eligibility_policy", None)
        for model in document["models"]:
            model.pop("generation_reliability", None)
            model.pop("winner_eligible", None)
            model.pop("winner_ineligibility_reasons", None)
        for entry in document["leaderboard"]:
            entry.pop("winner_eligible", None)
            entry.pop("winner_ineligibility_reasons", None)

    historical = _copy_artifacts(current, mutate=remove_additive_field)
    shown = render_run_show(historical)
    comparison = build_comparison(historical, current)

    assert "provider_error=1" in shown
    assert "Observed winner: good" in shown
    assert "Eligible winner: good" in shown
    assert "Observed winner: good" in render_report(historical)
    assert "unknown_detail" in shown
    assert comparison["compatibility"]["blockers"] == []
    assert comparison["model_deltas"]


async def test_comparison_reports_observed_and_eligible_winner_changes_as_non_blocking() -> None:
    run_a = await _artifacts()

    def mutate(document: dict[str, Any]) -> None:
        refusal = next(model for model in document["models"] if model["model"] == "refusal")
        refusal.update(
            {
                "successful_generations": 1,
                "completed_evaluations": 1,
                "winner_eligible": True,
                "winner_ineligibility_reasons": [],
            }
        )
        refusal_entry = next(
            entry for entry in document["leaderboard"] if entry["model"] == "refusal"
        )
        refusal_entry.update(
            {
                "weighted_mean_score": 100,
                "winner_eligible": True,
                "winner_ineligibility_reasons": [],
            }
        )
        document["leaderboard"].remove(refusal_entry)
        document["leaderboard"].insert(0, refusal_entry)

    comparison = build_comparison(run_a, _copy_artifacts(run_a, mutate=mutate))

    assert comparison["compatibility"]["blockers"] == []
    assert comparison["winner_changes"]["observed"]["changed"] is True
    assert comparison["winner_changes"]["eligible"]["changed"] is True
    assert comparison["winner_changes"]["observed"]["b"]["display_name"] == "refusal"
    assert "## Winner changes" in render_comparison_markdown(comparison)


async def test_show_and_report_do_not_fall_back_when_no_model_is_eligible() -> None:
    current = await _artifacts()

    def mutate(document: dict[str, Any]) -> None:
        good = next(model for model in document["models"] if model["model"] == "good")
        good["successful_generations"] = 0
        good["winner_eligible"] = False
        good["winner_ineligibility_reasons"] = ["incomplete_generation_success"]
        document["eligible_winner"] = None

    no_eligible = _copy_artifacts(current, mutate=mutate)

    assert "Observed winner: good" in render_run_show(no_eligible)
    assert "Eligible winner: No eligible winner" in render_run_show(no_eligible)
    assert "Eligible winner: No eligible winner" in render_report(no_eligible)


async def test_show_suppresses_running_headline_winners() -> None:
    current = await _artifacts()

    def mutate(document: dict[str, Any]) -> None:
        document["run"]["status"] = "running"
        document["run"]["incomplete"] = True
        document["winner_state"] = "suppressed_non_terminal"

    running = _copy_artifacts(current, mutate=mutate)

    shown = render_run_show(running)
    assert "Winners: suppressed until the benchmark reaches a terminal state" in shown
    assert "Eligible winner: pending" in shown


async def test_archive_is_deterministic_secret_free_and_verifiable(tmp_path: Path) -> None:
    artifacts = await _artifacts()
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"

    first = write_archive(artifacts, first_path, created_at=FIXED_ARCHIVE_TIME)
    second = write_archive(artifacts, second_path, created_at=FIXED_ARCHIVE_TIME)

    assert first == second
    assert verify_archive(first_path) == first
    assert first["archive_schema_version"] == "1"
    assert (
        first["results_sha256"]
        == hashlib.sha256((first_path / "results.json").read_bytes()).hexdigest()
    )
    assert (
        first["report_sha256"]
        == hashlib.sha256((first_path / "report.md").read_bytes()).hexdigest()
    )
    assert sorted(first["candidate_sha256"]) == sorted(
        path.relative_to(first_path).as_posix() for path in (first_path / "candidates").iterdir()
    )
    all_content = b"".join(
        path.read_bytes() for path in sorted(first_path.rglob("*")) if path.is_file()
    )
    assert b"configured-secret-value" not in all_content
    assert b"Authorization: Bearer" not in all_content
    with pytest.raises(BenchmarkProductError, match="not empty"):
        write_archive(artifacts, first_path)


async def test_archive_preserves_repeated_sample_indices_and_candidates(tmp_path: Path) -> None:
    original = await _artifacts()
    document = copy.deepcopy(original.document)
    source_sample = next(sample for sample in document["samples"] if sample["generation"])
    repeated_sample = copy.deepcopy(source_sample)
    repeated_id = str(uuid4())
    repeated_sample["benchmark_sample_id"] = repeated_id
    repeated_sample["sample_index"] = 2
    repeated_sample["generation"]["candidate_path"] = f"candidates/{repeated_id}.py"
    document["samples"].append(repeated_sample)
    document["samples"].sort(
        key=lambda sample: (
            str(sample["model_config_id"]),
            sample["task_id"],
            sample["sample_index"],
        )
    )
    document["run"]["samples_per_task"] = 2
    document["run"]["planned_sample_count"] += 1
    candidates = dict(original.candidates)
    original_candidate = next(iter(original.candidates.values()))
    candidates[f"{repeated_id}.py"] = original_candidate
    results_bytes = canonical_json_bytes(document)
    repeated = BenchmarkArtifacts(
        document=document,
        results_bytes=results_bytes,
        results_sha256=hashlib.sha256(results_bytes).hexdigest(),
        candidates=candidates,
    )

    archive = tmp_path / "repeated"
    manifest = write_archive(repeated, archive, created_at=FIXED_ARCHIVE_TIME)
    verified = verify_archive(archive)
    archived_document = json.loads((archive / "results.json").read_text(encoding="utf-8"))

    assert verified == manifest
    archived_pair = [
        sample
        for sample in archived_document["samples"]
        if sample["task_id"] == source_sample["task_id"]
        and str(sample["model_config_id"]) == str(source_sample["model_config_id"])
    ]
    assert [sample["sample_index"] for sample in archived_pair] == [1, 2]
    assert {sample["generation"]["candidate_path"] for sample in archived_pair} <= set(
        manifest["candidate_sha256"]
    )


async def test_archive_tamper_and_candidate_integrity_fail_precisely(tmp_path: Path) -> None:
    artifacts = await _artifacts()
    report_archive = tmp_path / "report-tamper"
    candidate_archive = tmp_path / "candidate-tamper"
    write_archive(artifacts, report_archive, created_at=FIXED_ARCHIVE_TIME)
    write_archive(artifacts, candidate_archive, created_at=FIXED_ARCHIVE_TIME)

    (report_archive / "report.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ArchiveIntegrityError, match=r"report\.md SHA-256 mismatch"):
        verify_archive(report_archive)

    candidate = next((candidate_archive / "candidates").iterdir())
    candidate.write_text("tampered", encoding="utf-8")
    with pytest.raises(ArchiveIntegrityError, match=r"candidates/.+ SHA-256 mismatch"):
        verify_archive(candidate_archive)

    manifest_path = candidate_archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    relative = candidate.relative_to(candidate_archive).as_posix()
    manifest["candidate_sha256"][relative] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    with pytest.raises(ArchiveIntegrityError, match=r"candidates/.+ source hash mismatch"):
        verify_archive(candidate_archive)


async def test_read_commands_never_construct_a_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_a = await _artifacts()
    run_b = _copy_artifacts(run_a)
    artifacts = {
        str(run_a.document["run"]["benchmark_run_id"]): run_a,
        str(run_b.document["run"]["benchmark_run_id"]): run_b,
    }

    def forbidden_provider(*_: object, **__: object) -> None:
        raise AssertionError("provider constructor must not be invoked")

    async def fake_export(run_id: Any, allow_incomplete: bool) -> BenchmarkArtifacts:
        del allow_incomplete
        return artifacts[str(run_id)]

    monkeypatch.setattr(cli, "OpenAICompatibleProvider", forbidden_provider)
    monkeypatch.setattr(cli, "_export", fake_export)

    assert (
        await cli._dispatch(
            argparse.Namespace(command="show", run_id=run_a.document["run"]["benchmark_run_id"])
        )
        == 0
    )
    assert (
        await cli._dispatch(
            argparse.Namespace(
                command="compare",
                run_a=run_a.document["run"]["benchmark_run_id"],
                run_b=run_b.document["run"]["benchmark_run_id"],
                json_output=True,
                output=None,
            )
        )
        == 0
    )
    archive_path = tmp_path / "archive"
    assert (
        await cli._dispatch(
            argparse.Namespace(
                command="archive",
                run_id=run_a.document["run"]["benchmark_run_id"],
                output=archive_path,
            )
        )
        == 0
    )
    assert await cli._dispatch(argparse.Namespace(command="verify-archive", path=archive_path)) == 0
    assert "Archive verified" in capsys.readouterr().out


async def test_incompatible_cli_comparison_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_a = await _artifacts()
    run_b = _copy_artifacts(
        run_a,
        mutate=lambda document: document["dataset"].__setitem__("fingerprint", "0" * 64),
    )
    artifacts = {
        str(run_a.document["run"]["benchmark_run_id"]): run_a,
        str(run_b.document["run"]["benchmark_run_id"]): run_b,
    }

    async def fake_export(run_id: Any, allow_incomplete: bool) -> BenchmarkArtifacts:
        del allow_incomplete
        return artifacts[str(run_id)]

    monkeypatch.setattr(cli, "_export", fake_export)
    exit_code = await cli._compare(
        run_a.document["run"]["benchmark_run_id"],
        run_b.document["run"]["benchmark_run_id"],
        json_output=False,
        output=None,
    )

    assert exit_code == 2
    assert "incompatible" in capsys.readouterr().err
