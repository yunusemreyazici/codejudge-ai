"""Immutable repository-versioned benchmark dataset registry."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from app.benchmarks.identity import dataset_fingerprint
from app.benchmarks.models import BenchmarkDataset, DatasetTaskEntry
from app.snapshots.fingerprints import task_fingerprint, tests_fingerprint
from app.tasks.registry import TaskNotFoundError, TaskRegistry


class DatasetRegistryError(RuntimeError):
    pass


class DatasetNotFoundError(DatasetRegistryError):
    pass


class _DatasetDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    dataset_version: str
    title: str
    description: str
    task_entries: tuple[DatasetTaskEntry, ...]


class BenchmarkDatasetRegistry:
    def __init__(self, definitions_path: Path, tasks: TaskRegistry) -> None:
        self._definitions_path = definitions_path
        self._tasks = tasks
        self._datasets: dict[tuple[str, str], BenchmarkDataset] = {}

    @classmethod
    def default(cls, tasks: TaskRegistry) -> BenchmarkDatasetRegistry:
        registry = cls(Path(__file__).parent / "datasets", tasks)
        registry.load()
        return registry

    def load(self) -> None:
        loaded: dict[tuple[str, str], BenchmarkDataset] = {}
        for path in sorted(self._definitions_path.glob("*.json")):
            try:
                definition = _DatasetDefinition.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, ValidationError) as error:
                raise DatasetRegistryError(f"Invalid benchmark dataset: {path.name}") from error
            entries = tuple(sorted(definition.task_entries, key=lambda item: item.task_id))
            if not entries:
                raise DatasetRegistryError(f"Benchmark dataset has no tasks: {path.name}")
            for entry in entries:
                self._validate_entry(entry)
            key = (definition.dataset_id, definition.dataset_version)
            if key in loaded:
                raise DatasetRegistryError(f"Duplicate benchmark dataset: {key[0]}@{key[1]}")
            loaded[key] = BenchmarkDataset(
                **definition.model_dump(exclude={"task_entries"}),
                task_entries=entries,
                dataset_fingerprint=dataset_fingerprint(*key, entries),
            )
        self._datasets = loaded

    def get(self, dataset_id: str, dataset_version: str) -> BenchmarkDataset:
        try:
            return self._datasets[(dataset_id, dataset_version)]
        except KeyError as error:
            raise DatasetNotFoundError(
                f"Unknown benchmark dataset: {dataset_id}@{dataset_version}"
            ) from error

    def _validate_entry(self, entry: DatasetTaskEntry) -> None:
        try:
            task = self._tasks.get(entry.task_id)
        except TaskNotFoundError as error:
            raise DatasetRegistryError(
                f"Dataset references unknown task: {entry.task_id}"
            ) from error
        current_tests = tests_fingerprint(task)
        current_task = task_fingerprint(task, current_tests)
        if task.specification.version != entry.task_version:
            raise DatasetRegistryError(f"Stale task version in dataset: {entry.task_id}")
        if current_tests != entry.tests_fingerprint:
            raise DatasetRegistryError(f"Stale tests fingerprint in dataset: {entry.task_id}")
        if current_task != entry.task_fingerprint:
            raise DatasetRegistryError(f"Stale task fingerprint in dataset: {entry.task_id}")
