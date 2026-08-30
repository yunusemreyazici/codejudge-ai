"""Load and resolve local task definitions without exposing their test sources."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.evaluator.models import Task


class TaskRegistryError(RuntimeError):
    """Base error for invalid or unavailable task definitions."""


class TaskNotFoundError(TaskRegistryError):
    def __init__(self, task_id: str, revision: int | None = None) -> None:
        identity = task_id if revision is None else f"{task_id}@{revision}"
        super().__init__(f"Unknown task: {identity}")
        self.task_id = task_id
        self.revision = revision


@dataclass(frozen=True, order=True, slots=True)
class TaskRevision:
    task_id: str
    revision: int

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id must not be empty")
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("task revision must be a positive integer")

    @property
    def identity(self) -> str:
        return f"{self.task_id}@{self.revision}"


@dataclass(frozen=True, slots=True)
class RegisteredTask:
    specification: Task
    tests_path: Path
    reference_path: Path | None = None
    revision: int = 1

    @property
    def identity(self) -> TaskRevision:
        return TaskRevision(self.specification.id, self.revision)

    @property
    def revision_identity(self) -> str:
        return self.identity.identity


DEFAULT_TASK_REVISIONS: Mapping[str, int] = {
    "async-batch-processor": 1,
    "circuit-breaker": 1,
    "config-layer-merge": 1,
    "dependency-resolver": 1,
    "frame-decoder": 2,
    "interval-reservation": 1,
    "logical-path": 1,
    "lru-cache": 1,
    "rate-limiter": 1,
    "retry-backoff": 2,
    "structured-event-parser": 1,
    "ttl-cache": 2,
}


class TaskRegistry:
    """In-memory index backed by version-controlled local task files."""

    def __init__(
        self,
        definitions_path: Path,
        default_timeout: float = 5.0,
        *,
        default_revisions: Mapping[str, int] | None = None,
    ) -> None:
        self._definitions_path = definitions_path
        self._default_timeout = default_timeout
        self._configured_defaults = dict(default_revisions or {})
        self._tasks: dict[TaskRevision, RegisteredTask] = {}
        self._default_revisions: dict[str, int] = {}

    @classmethod
    def default(cls, default_timeout: float = 5.0) -> TaskRegistry:
        definitions = Path(__file__).parent / "definitions"
        registry = cls(
            definitions,
            default_timeout,
            default_revisions=DEFAULT_TASK_REVISIONS,
        )
        registry.load()
        return registry

    def load(self) -> None:
        loaded: dict[TaskRevision, RegisteredTask] = {}
        task_files = [
            *(path for path in self._definitions_path.glob("*/task.yaml")),
            *(path for path in self._definitions_path.glob("*/revisions/*/task.yaml")),
        ]
        for task_file in sorted(task_files):
            revision = self._revision_from_path(task_file)
            registered = self._load_task(task_file, revision)
            identity = registered.identity
            if identity in loaded:
                raise TaskRegistryError(f"Duplicate task revision: {identity.identity}")
            loaded[identity] = registered
        defaults: dict[str, int] = {}
        task_ids = sorted({identity.task_id for identity in loaded})
        for task_id in task_ids:
            available = sorted(
                identity.revision for identity in loaded if identity.task_id == task_id
            )
            configured = self._configured_defaults.get(task_id)
            selected = available[-1] if configured is None else configured
            if TaskRevision(task_id, selected) not in loaded:
                raise TaskRegistryError(f"Unknown default task revision: {task_id}@{selected}")
            defaults[task_id] = selected
        unknown_defaults = set(self._configured_defaults) - set(task_ids)
        if unknown_defaults:
            unknown = sorted(unknown_defaults)[0]
            raise TaskRegistryError(f"Default revision configured for unknown task: {unknown}")
        self._tasks = loaded
        self._default_revisions = defaults

    def _load_task(self, task_file: Path, revision: int) -> RegisteredTask:
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
            revision=revision,
        )

    def get(self, task_id: str, revision: int | None = None) -> RegisteredTask:
        resolved_revision = self._default_revisions.get(task_id) if revision is None else revision
        if resolved_revision is None:
            raise TaskNotFoundError(task_id, revision)
        try:
            return self._tasks[TaskRevision(task_id, resolved_revision)]
        except KeyError as error:
            raise TaskNotFoundError(task_id, revision) from error

    def get_revision(self, task_id: str, revision: int) -> RegisteredTask:
        return self.get(task_id, revision=revision)

    def default_revision(self, task_id: str) -> int:
        try:
            return self._default_revisions[task_id]
        except KeyError as error:
            raise TaskNotFoundError(task_id) from error

    def revisions(self, task_id: str) -> tuple[int, ...]:
        revisions = tuple(
            sorted(identity.revision for identity in self._tasks if identity.task_id == task_id)
        )
        if not revisions:
            raise TaskNotFoundError(task_id)
        return revisions

    def list(self) -> list[Task]:
        return [self.get(task_id).specification for task_id in sorted(self._default_revisions)]

    def list_revisions(self) -> tuple[RegisteredTask, ...]:
        return tuple(self._tasks[identity] for identity in sorted(self._tasks))

    def __iter__(self) -> Iterator[RegisteredTask]:
        return iter(self.get(task_id) for task_id in sorted(self._default_revisions))

    def _revision_from_path(self, task_file: Path) -> int:
        try:
            relative = task_file.relative_to(self._definitions_path)
        except ValueError as error:
            raise TaskRegistryError(f"Task path is outside definitions: {task_file}") from error
        if len(relative.parts) == 2:
            return 1
        revision_name = relative.parts[-2]
        if revision_name.startswith("v"):
            revision_name = revision_name[1:]
        try:
            revision = int(revision_name)
        except ValueError as error:
            raise TaskRegistryError(
                f"Invalid task revision directory: {task_file.parent}"
            ) from error
        if revision < 1:
            raise TaskRegistryError(f"Invalid task revision directory: {task_file.parent}")
        return revision
