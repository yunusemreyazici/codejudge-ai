"""Reusable mutation-discrimination audit helpers for benchmark tasks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.models import BenchmarkDataset
from app.evaluator.models import RunnerResult
from app.tasks.registry import RegisteredTask


class CandidateRunner(Protocol):
    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult: ...


@dataclass(frozen=True, slots=True)
class SourceReplacement:
    old: str
    new: str


@dataclass(frozen=True, slots=True)
class MutationDefinition:
    task_id: str
    name: str
    replacements: tuple[SourceReplacement, ...]
    equivalent_reason: str | None = None
    survivor_reason: str | None = None

    def __post_init__(self) -> None:
        if self.equivalent_reason is not None and self.survivor_reason is not None:
            raise ValueError("A mutation cannot be both equivalent and a known survivor.")

    def materialize(self, reference_source: str) -> str:
        source = reference_source
        for replacement in self.replacements:
            occurrences = source.count(replacement.old)
            if occurrences != 1:
                raise ValueError(
                    f"Mutation {self.task_id}/{self.name} expected one source match, "
                    f"found {occurrences}."
                )
            source = source.replace(replacement.old, replacement.new, 1)
        return source


class MutationClassification(StrEnum):
    KILLED = "killed"
    SURVIVED = "survived"
    EQUIVALENT = "equivalent"
    INVALID = "infrastructure_invalid"


@dataclass(frozen=True, slots=True)
class MutationOutcome:
    task_id: str
    name: str
    classification: MutationClassification
    passed: int
    failed: int
    total: int
    duration_seconds: float
    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class MutationSummary:
    total_generated: int
    valid_mutants: int
    killed: int
    survived: int
    equivalent: int
    invalid: int

    @property
    def mutation_score(self) -> float:
        if self.valid_mutants == 0:
            return 0.0
        return self.killed / self.valid_mutants


async def execute_mutation(
    runner: CandidateRunner,
    task: RegisteredTask,
    mutation: MutationDefinition,
) -> MutationOutcome:
    if mutation.task_id != task.specification.id:
        raise ValueError("Mutation task ID does not match the registered task.")
    if task.reference_path is None:
        raise ValueError(f"Task has no reference source: {mutation.task_id}")
    try:
        source = mutation.materialize(task.reference_path.read_text(encoding="utf-8"))
        compile(source, f"<mutation:{mutation.task_id}/{mutation.name}>", "exec")
    except (OSError, SyntaxError, ValueError) as error:
        return MutationOutcome(
            task_id=mutation.task_id,
            name=mutation.name,
            classification=MutationClassification.INVALID,
            passed=0,
            failed=0,
            total=0,
            duration_seconds=0.0,
            diagnostic=str(error),
        )

    result = await runner.evaluate(task, source)
    infrastructure_diagnostic = (
        result.infrastructure_error
        or result.sandbox_error
        or ("timed out" if result.timed_out else None)
        or ("OOM killed" if result.oom_killed else None)
        or ("syntax error" if result.syntax_error else None)
        or ("import error" if result.import_error else None)
    )
    if infrastructure_diagnostic is not None or result.total == 0:
        return MutationOutcome(
            task_id=mutation.task_id,
            name=mutation.name,
            classification=MutationClassification.INVALID,
            passed=result.passed,
            failed=result.failed,
            total=result.total,
            duration_seconds=result.duration_seconds,
            diagnostic=infrastructure_diagnostic or "evaluator reported no authoritative cases",
        )

    if result.failed:
        classification = MutationClassification.KILLED
    elif mutation.equivalent_reason is not None:
        classification = MutationClassification.EQUIVALENT
    else:
        classification = MutationClassification.SURVIVED
    return MutationOutcome(
        task_id=mutation.task_id,
        name=mutation.name,
        classification=classification,
        passed=result.passed,
        failed=result.failed,
        total=result.total,
        duration_seconds=result.duration_seconds,
    )


async def execute_dataset_mutation(
    runner: CandidateRunner,
    datasets: BenchmarkDatasetRegistry,
    dataset: BenchmarkDataset,
    mutation: MutationDefinition,
) -> MutationOutcome:
    """Audit the exact task revision selected by an immutable dataset entry."""

    _, task = datasets.resolve_dataset_task(dataset, mutation.task_id)
    return await execute_mutation(runner, task, mutation)


def summarize_mutations(outcomes: list[MutationOutcome]) -> MutationSummary:
    counts = {
        classification: sum(outcome.classification == classification for outcome in outcomes)
        for classification in MutationClassification
    }
    killed = counts[MutationClassification.KILLED]
    survived = counts[MutationClassification.SURVIVED]
    return MutationSummary(
        total_generated=len(outcomes),
        valid_mutants=killed + survived,
        killed=killed,
        survived=survived,
        equivalent=counts[MutationClassification.EQUIVALENT],
        invalid=counts[MutationClassification.INVALID],
    )
