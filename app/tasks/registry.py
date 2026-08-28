"""Load and resolve local task definitions without exposing their test sources."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.evaluator.models import Task


class TaskRegistryError(RuntimeError):
    """Base error for invalid or unavailable task definitions."""


class TaskNotFoundError(TaskRegistryError):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"Unknown task: {task_id}")
        self.task_id = task_id


@dataclass(frozen=True, slots=True)
class RegisteredTask:
    specification: Task
    tests_path: Path
    reference_path: Path | None = None


class TaskRegistry:
    """In-memory index backed by version-controlled local task files."""

    def __init__(self, definitions_path: Path, default_timeout: float = 5.0) -> None:
        self._definitions_path = definitions_path
        self._default_timeout = default_timeout
        self._tasks: dict[str, RegisteredTask] = {}

    @classmethod
    def default(cls, default_timeout: float = 5.0) -> TaskRegistry:
        definitions = Path(__file__).parent / "definitions"
        registry = cls(definitions, default_timeout)
        registry.load()
        return registry

    def load(self) -> None:
        loaded: dict[str, RegisteredTask] = {}
        for task_file in sorted(self._definitions_path.glob("*/task.yaml")):
            registered = self._load_task(task_file)
            task_id = registered.specification.id
            if task_id in loaded:
                raise TaskRegistryError(f"Duplicate task id: {task_id}")
            loaded[task_id] = registered
        self._tasks = loaded

    def _load_task(self, task_file: Path) -> RegisteredTask:
        try:
            raw: object = json.loads(task_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise TaskRegistryError(f"Cannot load task definition {task_file}") from error

        if not isinstance(raw, dict):
            raise TaskRegistryError(f"Task definition must be an object: {task_file}")
        raw.setdefault("timeout_seconds", self._default_timeout)

        try:
            specification = Task.model_validate(raw)
        except ValidationError as error:
            raise TaskRegistryError(f"Invalid task definition {task_file}") from error

        tests_path = task_file.parent / "tests"
        if not tests_path.is_dir() or not any(tests_path.glob("test_*.py")):
            raise TaskRegistryError(f"Task has no tests: {specification.id}")
        reference_path = task_file.parent / "reference" / "solution.py"
        return RegisteredTask(
            specification=specification,
            tests_path=tests_path,
            reference_path=reference_path if reference_path.is_file() else None,
        )

    def get(self, task_id: str) -> RegisteredTask:
        try:
            return self._tasks[task_id]
        except KeyError as error:
            raise TaskNotFoundError(task_id) from error

    def list(self) -> list[Task]:
        return [registered.specification for registered in self._tasks.values()]

    def __iter__(self) -> Iterator[RegisteredTask]:
        return iter(self._tasks.values())
