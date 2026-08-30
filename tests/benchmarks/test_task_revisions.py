from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.benchmarks.datasets import BenchmarkDatasetRegistry, DatasetRegistryError
from app.benchmarks.identity import dataset_fingerprint
from app.benchmarks.models import DatasetTaskEntry
from app.benchmarks.run_config import build_plan, load_benchmark_config
from app.evaluator.engine import EvaluationEngine
from app.evaluator.models import EvaluationRequest, RunnerResult
from app.runners.trusted_harness import HarnessProtocolError, TrustedOfficialHarness
from app.snapshots.fingerprints import task_fingerprint
from app.snapshots.fingerprints import tests_fingerprint as _tests_fingerprint
from app.tasks.registry import RegisteredTask
from tests.tasks.mutation_audit import (
    MutationClassification,
    MutationDefinition,
    SourceReplacement,
    execute_dataset_mutation,
)
from tests.tasks.revision_fixtures import build_revision_registry


def _entry(task: RegisteredTask, *, explicit_revision: bool) -> DatasetTaskEntry:
    tests_hash = _tests_fingerprint(task)
    return DatasetTaskEntry(
        task_id=task.specification.id,
        task_revision=task.revision if explicit_revision else None,
        task_version=task.specification.version,
        task_fingerprint=task_fingerprint(task, tests_hash),
        tests_fingerprint=tests_hash,
        weight=1,
    )


def _datasets(root: Path, *, default_revision: int = 2):
    tasks = build_revision_registry(root, default_revision=default_revision)
    definitions = root / "datasets"
    definitions.mkdir()
    first = _entry(tasks.get_revision("sample", 1), explicit_revision=False)
    second = _entry(tasks.get_revision("sample", 2), explicit_revision=True)
    for version, entry in (("1", first), ("2", second)):
        payload = {
            "dataset_id": "synthetic",
            "dataset_version": version,
            "title": f"Synthetic revision {version}",
            "description": "Test-only immutable revision fixture.",
            "task_entries": [entry.model_dump(mode="json", exclude_none=True)],
        }
        (definitions / f"synthetic_v{version}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    datasets = BenchmarkDatasetRegistry(definitions, tasks)
    datasets.load()
    return tasks, datasets


def test_datasets_resolve_exact_revisions_independently_of_current_default(tmp_path: Path) -> None:
    tasks, datasets = _datasets(tmp_path, default_revision=2)
    historical = datasets.get("synthetic", "1")
    revised = datasets.get("synthetic", "2")

    assert tasks.get("sample").revision == 2
    assert datasets.resolve_task(historical.task_entries[0]).revision == 1
    assert datasets.resolve_task(revised.task_entries[0]).revision == 2
    assert historical.task_entries[0].task_revision is None
    assert historical.task_entries[0].resolved_task_revision == 1
    assert revised.task_entries[0].task_revision == 2
    assert historical.dataset_fingerprint != revised.dataset_fingerprint
    first_tests = _tests_fingerprint(tasks.get_revision("sample", 1))
    second_tests = _tests_fingerprint(tasks.get_revision("sample", 2))
    assert task_fingerprint(tasks.get_revision("sample", 1), first_tests) != task_fingerprint(
        tasks.get_revision("sample", 2), second_tests
    )


def test_dataset_fingerprint_binds_explicit_revision_but_preserves_legacy_encoding(
    tmp_path: Path,
) -> None:
    _, datasets = _datasets(tmp_path)
    historical = datasets.get("synthetic", "1")
    entry = historical.task_entries[0]

    assert "task_revision" not in entry.model_dump(mode="json", exclude_none=True)
    explicit_revision = entry.model_copy(update={"task_revision": 2})
    changed = dataset_fingerprint(
        historical.dataset_id,
        historical.dataset_version,
        [explicit_revision],
    )

    assert changed != historical.dataset_fingerprint


def test_changing_current_revision_cannot_change_historical_dataset_resolution(
    tmp_path: Path,
) -> None:
    _, current_datasets = _datasets(tmp_path / "current", default_revision=2)
    _, old_default_datasets = _datasets(tmp_path / "old-default", default_revision=1)
    current = current_datasets.get("synthetic", "1")
    old_default = old_default_datasets.get("synthetic", "1")

    assert current.dataset_fingerprint == old_default.dataset_fingerprint
    assert current_datasets.resolve_task(current.task_entries[0]).revision == 1
    assert old_default_datasets.resolve_task(old_default.task_entries[0]).revision == 1


def test_dataset_unknown_revision_fails_instead_of_using_current(tmp_path: Path) -> None:
    tasks, datasets = _datasets(tmp_path)
    definition = json.loads(
        (tmp_path / "datasets" / "synthetic_v2.json").read_text(encoding="utf-8")
    )
    definition["dataset_version"] = "missing"
    definition["task_entries"][0]["task_revision"] = 3
    (tmp_path / "datasets" / "missing.json").write_text(json.dumps(definition), encoding="utf-8")

    with pytest.raises(DatasetRegistryError, match=r"sample@3"):
        BenchmarkDatasetRegistry(tmp_path / "datasets", tasks).load()

    assert datasets.get("synthetic", "2").dataset_version == "2"


def test_benchmark_planning_uses_dataset_revision_not_current_default(tmp_path: Path) -> None:
    tasks, datasets = _datasets(tmp_path, default_revision=2)
    config = load_benchmark_config(Path("benchmark-configs/real-smoke.example.yaml"))
    config = config.model_copy(
        update={"dataset": config.dataset.model_copy(update={"id": "synthetic", "version": "1"})}
    )

    plan = build_plan(config, tasks=tasks, datasets=datasets, environment={})
    historical = datasets.get("synthetic", "1")

    assert tasks.get("sample").revision == 2
    assert plan.dataset_fingerprint == historical.dataset_fingerprint
    assert plan.task_count == 1
    assert plan.planned_generations == 2


class _Runner:
    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
        return RunnerResult(
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0,
            passed=1,
            failed=0,
            total=1,
        )


def test_evaluator_request_can_select_exact_revision_and_default_remains_compatible(
    tmp_path: Path,
) -> None:
    tasks = build_revision_registry(tmp_path, default_revision=2)
    engine = EvaluationEngine(tasks, {"python": _Runner()}, max_code_size=1_000)

    exact = engine.prepare_request(
        EvaluationRequest(
            task_id="sample",
            language="python",
            code="def marker(): return '1.0'",
        ),
        task_revision=1,
    )
    current = engine.prepare_request(
        EvaluationRequest(
            task_id="sample",
            language="python",
            code="def marker(): return '2.0'",
        )
    )

    assert exact.revision == 1
    assert current.revision == 2


class _MutationRunner:
    def __init__(self) -> None:
        self.revisions: list[int] = []

    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
        self.revisions.append(task.revision)
        return RunnerResult(
            exit_code=1,
            stdout="",
            stderr="",
            duration_seconds=0,
            passed=0,
            failed=1,
            total=1,
        )


async def test_mutation_audit_uses_dataset_selected_revision(tmp_path: Path) -> None:
    _, datasets = _datasets(tmp_path, default_revision=2)
    historical = datasets.get("synthetic", "1")
    mutation = MutationDefinition(
        task_id="sample",
        name="returns_wrong_revision_marker",
        replacements=(SourceReplacement('return "1.0"', 'return "wrong"'),),
    )
    runner = _MutationRunner()

    outcome = await execute_dataset_mutation(runner, datasets, historical, mutation)

    assert runner.revisions == [1]
    assert outcome.classification is MutationClassification.KILLED


async def test_trusted_harness_refuses_unregistered_revision_without_fallback() -> None:
    with pytest.raises(HarnessProtocolError, match=r"lru-cache@2"):
        await TrustedOfficialHarness().evaluate("lru-cache", 2, object())  # type: ignore[arg-type]
