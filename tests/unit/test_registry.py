import json
from pathlib import Path

import pytest

from app.tasks.registry import TaskNotFoundError, TaskRegistry, TaskRegistryError
from tests.tasks.revision_fixtures import build_revision_registry


def test_default_registry_loads_complete_task_portfolio() -> None:
    registry = TaskRegistry.default()

    tasks = registry.list()

    assert [task.id for task in tasks] == [
        "async-batch-processor",
        "circuit-breaker",
        "config-layer-merge",
        "dependency-resolver",
        "frame-decoder",
        "interval-reservation",
        "logical-path",
        "lru-cache",
        "rate-limiter",
        "retry-backoff",
        "structured-event-parser",
        "ttl-cache",
    ]
    assert registry.get("lru-cache").specification.entrypoint == "solution:LRUCache"
    assert registry.get("lru-cache").tests_path.is_dir()
    assert all(task.reference_path is not None for task in registry)


def test_unknown_task_raises_typed_error() -> None:
    registry = TaskRegistry.default()

    with pytest.raises(TaskNotFoundError, match="missing"):
        registry.get("missing")


def test_registry_uses_default_timeout_when_omitted(tmp_path: Path) -> None:
    task_directory = tmp_path / "sample"
    tests_directory = task_directory / "tests"
    tests_directory.mkdir(parents=True)
    (tests_directory / "test_sample.py").write_text("def test_ok(): assert True\n")
    definition = {
        "id": "sample",
        "title": "Sample",
        "description": "Sample task",
        "language": "python",
    }
    (task_directory / "task.yaml").write_text(json.dumps(definition))
    registry = TaskRegistry(tmp_path, default_timeout=2.5)

    registry.load()

    assert registry.get("sample").specification.timeout_seconds == 2.5


def test_registry_rejects_task_without_tests(tmp_path: Path) -> None:
    task_directory = tmp_path / "sample"
    task_directory.mkdir()
    definition = {
        "id": "sample",
        "title": "Sample",
        "description": "Sample task",
        "language": "python",
        "timeout_seconds": 1,
    }
    (task_directory / "task.yaml").write_text(json.dumps(definition))
    registry = TaskRegistry(tmp_path)

    with pytest.raises(TaskRegistryError, match="no tests"):
        registry.load()


def test_two_immutable_revisions_coexist_with_exact_and_default_lookup(tmp_path: Path) -> None:
    registry = build_revision_registry(tmp_path, default_revision=2)

    first = registry.get_revision("sample", 1)
    second = registry.get("sample", revision=2)

    assert first.revision_identity == "sample@1"
    assert second.revision_identity == "sample@2"
    assert first.specification.title == "Sample revision one"
    assert second.specification.title == "Sample revision two"
    assert registry.get("sample") == second
    assert registry.default_revision("sample") == 2
    assert registry.revisions("sample") == (1, 2)
    assert [task.revision_identity for task in registry.list_revisions()] == [
        "sample@1",
        "sample@2",
    ]
    assert [task.id for task in registry.list()] == ["sample"]


def test_unknown_revision_fails_without_latest_fallback(tmp_path: Path) -> None:
    registry = build_revision_registry(tmp_path)

    with pytest.raises(TaskNotFoundError, match=r"sample@3"):
        registry.get_revision("sample", 3)


def test_duplicate_revision_registration_is_rejected(tmp_path: Path) -> None:
    registry = build_revision_registry(tmp_path)
    duplicate = tmp_path / "definitions" / "sample" / "revisions" / "1"
    duplicate.mkdir(parents=True)
    (duplicate / "task.yaml").write_text(
        (tmp_path / "definitions" / "sample" / "task.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    tests = duplicate / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    with pytest.raises(TaskRegistryError, match=r"Duplicate task revision: sample@1"):
        registry.load()


def test_default_selection_is_explicit_and_does_not_change_exact_lookup(tmp_path: Path) -> None:
    current = build_revision_registry(tmp_path / "current", default_revision=2)
    historical = build_revision_registry(tmp_path / "historical", default_revision=1)

    assert current.get("sample").revision == 2
    assert historical.get("sample").revision == 1
    assert current.get_revision("sample", 1).specification == historical.get("sample").specification
