from __future__ import annotations

from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.prompts import coding_payload
from app.evaluator.engine import EvaluationEngine
from app.evaluator.models import EvaluationRequest, RunnerResult
from app.runners.trusted_harness import OFFICIAL_CASES_BY_REVISION
from app.snapshots.fingerprints import tests_fingerprint as _tests_fingerprint
from app.tasks.registry import RegisteredTask, TaskRegistry

HISTORICAL_FINGERPRINTS = {
    "1": "866151a6d4d628805b37de98a67cdb02c646ecce16c2fc037e4c86d130234ebb",
    "2": "ee0f631d6810c039e84d90d9f2b77f20dcabbe27bef0af600695ab9cb1111988",
    "3": "1191d27db4643e9c18a0063ea9da1d2fb56fc363f0d2146740b53eee05e94522",
}
CORE_V4_FINGERPRINT = "ed5b1a5c0263ca6d172c31c15de910795815247f238cfefc3975624ce4f296d0"
AFFECTED_TASKS = {"frame-decoder", "retry-backoff", "ttl-cache"}


def _revision_map(version: str) -> dict[str, int]:
    tasks = TaskRegistry.default()
    dataset = BenchmarkDatasetRegistry.default(tasks).get("codejudge-core", version)
    return {entry.task_id: entry.resolved_task_revision for entry in dataset.task_entries}


def test_core_v4_has_exact_mixed_revision_map_and_stable_identity() -> None:
    tasks = TaskRegistry.default()
    datasets = BenchmarkDatasetRegistry.default(tasks)
    historical = datasets.get("codejudge-core", "3")
    hardened = datasets.get("codejudge-core", "4")

    assert len(historical.task_entries) == len(hardened.task_entries) == 12
    assert set(_revision_map("3").values()) == {1}
    assert _revision_map("4") == {
        task_id: 2 if task_id in AFFECTED_TASKS else 1 for task_id in _revision_map("3")
    }
    assert hardened.dataset_fingerprint == CORE_V4_FINGERPRINT
    assert all(entry.task_revision is not None for entry in hardened.task_entries)


def test_released_dataset_fingerprints_and_revision_one_resolution_are_frozen() -> None:
    tasks = TaskRegistry.default()
    datasets = BenchmarkDatasetRegistry.default(tasks)

    for version, expected in HISTORICAL_FINGERPRINTS.items():
        dataset = datasets.get("codejudge-core", version)
        assert dataset.dataset_fingerprint == expected
        assert {entry.resolved_task_revision for entry in dataset.task_entries} == {1}

    assert tasks.default_revision("frame-decoder") == 2
    assert tasks.default_revision("retry-backoff") == 2
    assert tasks.default_revision("ttl-cache") == 2


def test_revision_two_preserves_public_contract_and_reference_bytes() -> None:
    tasks = TaskRegistry.default()

    for task_id in AFFECTED_TASKS:
        original = tasks.get_revision(task_id, 1)
        hardened = tasks.get_revision(task_id, 2)
        assert hardened.specification == original.specification
        assert original.reference_path is not None
        assert hardened.reference_path is not None
        assert hardened.reference_path.read_bytes() == original.reference_path.read_bytes()
        assert _tests_fingerprint(hardened) != _tests_fingerprint(original)


def test_prompt_resolution_and_trusted_harness_use_core_v4_revision() -> None:
    tasks = TaskRegistry.default()
    datasets = BenchmarkDatasetRegistry.default(tasks)
    hardened = datasets.get("codejudge-core", "4")

    for entry in hardened.task_entries:
        task = datasets.resolve_task(entry)
        payload = coding_payload(task.specification)
        assert task.revision == entry.resolved_task_revision
        public_task = payload["public_task"]
        assert isinstance(public_task, dict)
        assert public_task["id"] == entry.task_id
        assert (entry.task_id, entry.resolved_task_revision) in OFFICIAL_CASES_BY_REVISION

    for task_id in AFFECTED_TASKS:
        assert len(OFFICIAL_CASES_BY_REVISION[(task_id, 2)]) == len(
            OFFICIAL_CASES_BY_REVISION[(task_id, 1)]
        )
        assert OFFICIAL_CASES_BY_REVISION[(task_id, 2)] != OFFICIAL_CASES_BY_REVISION[(task_id, 1)]


class _Runner:
    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
        raise AssertionError("prepare_request must not execute source")


def test_evaluator_can_replay_core_v3_and_execute_core_v4_exactly() -> None:
    tasks = TaskRegistry.default()
    datasets = BenchmarkDatasetRegistry.default(tasks)
    engine = EvaluationEngine(tasks, {"python": _Runner()}, max_code_size=1_000)
    request = EvaluationRequest(
        task_id="ttl-cache",
        language="python",
        code="class TTLCache: pass",
    )

    for version, expected_revision in (("3", 1), ("4", 2)):
        dataset = datasets.get("codejudge-core", version)
        entry, task = datasets.resolve_dataset_task(dataset, "ttl-cache")
        prepared = engine.prepare_request(
            request,
            task_revision=entry.resolved_task_revision,
        )
        assert task.revision == prepared.revision == expected_revision
