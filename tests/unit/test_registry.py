import json
from pathlib import Path

import pytest

from app.tasks.registry import TaskNotFoundError, TaskRegistry, TaskRegistryError


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
