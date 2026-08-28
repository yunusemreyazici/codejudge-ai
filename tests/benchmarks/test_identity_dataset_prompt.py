import json
from pathlib import Path

import pytest

from app.benchmarks.datasets import BenchmarkDatasetRegistry, DatasetRegistryError
from app.benchmarks.identity import (
    benchmark_run_fingerprint,
    dataset_fingerprint,
    model_configuration_fingerprint,
)
from app.benchmarks.models import BenchmarkModelRequest
from app.benchmarks.prompts import CODING_PROMPT_HASH, coding_payload
from app.tasks.registry import TaskRegistry


def test_builtin_dataset_is_validated_and_stably_fingerprinted() -> None:
    tasks = TaskRegistry.default()
    first = BenchmarkDatasetRegistry.default(tasks).get("codejudge-core", "1")
    second = BenchmarkDatasetRegistry.default(tasks).get("codejudge-core", "1")

    assert first == second
    assert len(first.task_entries) == 1
    assert first.task_entries[0].task_id == "lru-cache"
    assert first.dataset_fingerprint == dataset_fingerprint(
        first.dataset_id, first.dataset_version, first.task_entries
    )
    assert first.dataset_fingerprint == (
        "866151a6d4d628805b37de98a67cdb02c646ecce16c2fc037e4c86d130234ebb"
    )


def test_dataset_v2_is_diverse_stable_and_does_not_mutate_v1() -> None:
    registry = BenchmarkDatasetRegistry.default(TaskRegistry.default())
    first = registry.get("codejudge-core", "1")
    second = registry.get("codejudge-core", "2")

    assert [entry.task_id for entry in first.task_entries] == ["lru-cache"]
    assert [entry.task_id for entry in second.task_entries] == [
        "async-batch-processor",
        "circuit-breaker",
        "dependency-resolver",
        "lru-cache",
        "rate-limiter",
        "retry-backoff",
        "ttl-cache",
    ]
    assert all(entry.weight == 1 for entry in second.task_entries)
    assert second.dataset_fingerprint == (
        "ee0f631d6810c039e84d90d9f2b77f20dcabbe27bef0af600695ab9cb1111988"
    )
    assert first.dataset_fingerprint != second.dataset_fingerprint


def test_dataset_registry_rejects_duplicate_task_entries(tmp_path: Path) -> None:
    tasks = TaskRegistry.default()
    task = tasks.get("lru-cache")
    dataset = BenchmarkDatasetRegistry.default(tasks).get("codejudge-core", "1")
    entry = dataset.task_entries[0].model_dump(mode="json")
    definition = {
        "dataset_id": "duplicate",
        "dataset_version": "1",
        "title": "Duplicate",
        "description": "Invalid duplicate task fixture.",
        "task_entries": [entry, entry],
    }
    (tmp_path / "duplicate.json").write_text(json.dumps(definition), encoding="utf-8")

    with pytest.raises(DatasetRegistryError, match="Duplicate task"):
        BenchmarkDatasetRegistry(tmp_path, tasks).load()

    assert task.specification.id == "lru-cache"


def test_dataset_fingerprint_changes_with_weight_and_is_order_canonical() -> None:
    dataset = BenchmarkDatasetRegistry.default(TaskRegistry.default()).get("codejudge-core", "1")
    entry = dataset.task_entries[0]

    changed = dataset_fingerprint(
        dataset.dataset_id,
        dataset.dataset_version,
        [entry.model_copy(update={"weight": 2.0})],
    )

    assert changed != dataset.dataset_fingerprint


def test_model_and_run_fingerprints_cover_every_fairness_control() -> None:
    model = BenchmarkModelRequest(provider_id="fake", model="good", temperature=0, seed=7)
    model_hash = model_configuration_fingerprint(model, CODING_PROMPT_HASH)
    changed_model_hash = model_configuration_fingerprint(
        model.model_copy(update={"temperature": 0.2}), CODING_PROMPT_HASH
    )
    baseline = benchmark_run_fingerprint(
        dataset_hash="a" * 64,
        ordered_model_hashes=[model_hash],
        samples_per_task=1,
        coding_prompt_version="1",
        coding_prompt_hash=CODING_PROMPT_HASH,
        evaluator_hash="b" * 64,
        policy_version="1",
    )

    assert changed_model_hash != model_hash
    for changes in (
        {"dataset_hash": "c" * 64},
        {"ordered_model_hashes": [changed_model_hash]},
        {"samples_per_task": 2},
        {"coding_prompt_version": "2"},
        {"coding_prompt_hash": "d" * 64},
        {"evaluator_hash": "e" * 64},
        {"policy_version": "2"},
    ):
        arguments = {
            "dataset_hash": "a" * 64,
            "ordered_model_hashes": [model_hash],
            "samples_per_task": 1,
            "coding_prompt_version": "1",
            "coding_prompt_hash": CODING_PROMPT_HASH,
            "evaluator_hash": "b" * 64,
            "policy_version": "1",
        }
        arguments.update(changes)
        assert benchmark_run_fingerprint(**arguments) != baseline


def test_coding_prompt_payload_contains_only_public_task_data() -> None:
    registered = TaskRegistry.default().get("lru-cache")

    payload = coding_payload(registered.specification)
    rendered = str(payload).lower()

    assert registered.specification.description.lower() in rendered
    assert "required_entrypoint" in rendered
    assert "hidden" not in rendered
    assert "reference" not in rendered
    assert "tests_path" not in rendered
    assert "solution.py" not in rendered


def test_every_v2_coding_payload_excludes_private_evaluator_material() -> None:
    tasks = TaskRegistry.default()
    dataset = BenchmarkDatasetRegistry.default(tasks).get("codejudge-core", "2")

    for entry in dataset.task_entries:
        payload = coding_payload(tasks.get(entry.task_id).specification)
        rendered = json.dumps(payload, sort_keys=True).lower()
        assert "reference" not in rendered
        assert "hidden test" not in rendered
        assert "tests_path" not in rendered
        assert "solution.py" not in rendered
