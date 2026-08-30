from __future__ import annotations

import pytest

from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.runners.python_runner import PythonRunner
from app.runners.trusted_harness import OFFICIAL_CASES_BY_REVISION
from app.tasks.registry import TaskRegistry
from tests.tasks.candidates import INCORRECT_CANDIDATES

TASKS = TaskRegistry.default()
DATASETS = BenchmarkDatasetRegistry.default(TASKS)
CORE_V4 = DATASETS.get("codejudge-core", "4")
TASK_IDS = tuple(entry.task_id for entry in CORE_V4.task_entries)


def _core_v4_task(task_id: str):
    return DATASETS.resolve_dataset_task(CORE_V4, task_id)[1]


@pytest.mark.parametrize("task_id", TASK_IDS)
async def test_core_v4_trusted_reference_passes_exact_canonical_revision(task_id: str) -> None:
    task = _core_v4_task(task_id)
    assert task.reference_path is not None

    result = await PythonRunner().evaluate(task, task.reference_path.read_text(encoding="utf-8"))

    assert result.infrastructure_error is None
    assert result.timed_out is False
    assert result.failed == 0
    assert result.passed == result.total
    assert result.total == len(OFFICIAL_CASES_BY_REVISION[(task_id, task.revision)])


@pytest.mark.parametrize("task_id", TASK_IDS)
async def test_core_v4_incorrect_candidate_fails_authoritative_behavior(task_id: str) -> None:
    task = _core_v4_task(task_id)

    result = await PythonRunner().evaluate(task, INCORRECT_CANDIDATES[task_id])

    assert result.infrastructure_error is None
    assert result.timed_out is False
    assert result.syntax_error is False
    assert result.import_error is False
    assert result.total > 0
    assert result.failed > 0
    assert result.passed < result.total
