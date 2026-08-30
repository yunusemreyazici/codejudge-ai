from __future__ import annotations

from pathlib import Path

import pytest

from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.run_config import build_plan, load_benchmark_config
from app.evaluator.models import RunnerResult
from app.evaluator.models import TestResult as EvaluationTests
from app.evaluator.scoring import calculate_final_score, calculate_score
from app.runners.python_runner import PythonRunner
from app.tasks.registry import RegisteredTask, TaskRegistry
from tests.tasks.mutation_audit import (
    MutationClassification,
    MutationDefinition,
    MutationOutcome,
    execute_mutation,
    summarize_mutations,
)
from tests.tasks.mutation_catalog import MUTATIONS, MUTATIONS_BY_TASK

EXPECTED_DATASET_FINGERPRINTS = {
    "1": "866151a6d4d628805b37de98a67cdb02c646ecce16c2fc037e4c86d130234ebb",
    "2": "ee0f631d6810c039e84d90d9f2b77f20dcabbe27bef0af600695ab9cb1111988",
    "3": "1191d27db4643e9c18a0063ea9da1d2fb56fc363f0d2146740b53eee05e94522",
}


def _expected_classification(mutation: MutationDefinition) -> MutationClassification:
    equivalent_reason = mutation.equivalent_reason
    survivor_reason = mutation.survivor_reason
    if equivalent_reason is not None:
        return MutationClassification.EQUIVALENT
    if survivor_reason is not None:
        return MutationClassification.SURVIVED
    return MutationClassification.KILLED


def test_every_core_v3_task_has_at_least_five_meaningful_mutants() -> None:
    tasks = TaskRegistry.default()
    dataset = BenchmarkDatasetRegistry.default(tasks).get("codejudge-core", "3")
    dataset_task_ids = {entry.task_id for entry in dataset.task_entries}

    assert set(MUTATIONS_BY_TASK) == dataset_task_ids
    for mutations in MUTATIONS_BY_TASK.values():
        meaningful = [mutation for mutation in mutations if mutation.equivalent_reason is None]
        assert len(meaningful) >= 5
        assert len({mutation.name for mutation in mutations}) == len(mutations)


def test_equivalent_and_surviving_mutants_have_explicit_reasons() -> None:
    equivalents = [mutation for mutation in MUTATIONS if mutation.equivalent_reason is not None]
    survivors = [mutation for mutation in MUTATIONS if mutation.survivor_reason is not None]

    assert {mutation.name for mutation in equivalents} == {
        "relies_on_gather_for_batch_cancellation_cleanup",
        "prunes_at_most_one_expired_event_per_call",
        "stable_sort_by_timestamp",
    }
    assert {mutation.name for mutation in survivors} == {
        "counts_utf8_bytes_instead_of_characters",
        "rejects_equal_base_and_cap",
        "expired_entries_consume_capacity_on_put",
        "delete_does_not_purge_expired_entries",
    }
    assert all(mutation.equivalent_reason for mutation in equivalents)
    assert all(mutation.survivor_reason for mutation in survivors)


@pytest.mark.parametrize(
    "mutation",
    MUTATIONS,
    ids=lambda mutation: f"{mutation.task_id}-{mutation.name}",
)
async def test_registered_mutation_has_a_deterministic_audit_outcome(
    mutation: MutationDefinition,
) -> None:
    registry = TaskRegistry.default()

    outcome = await execute_mutation(PythonRunner(), registry.get(mutation.task_id), mutation)

    assert outcome.classification == _expected_classification(mutation)
    assert outcome.total > 0
    assert outcome.diagnostic is None


class _InfrastructureFailureRunner:
    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
        return RunnerResult(
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=0,
            passed=0,
            failed=0,
            total=0,
            infrastructure_error="test infrastructure unavailable",
        )


async def test_infrastructure_failure_is_invalid_not_killed() -> None:
    task = TaskRegistry.default().get(MUTATIONS[0].task_id)

    outcome = await execute_mutation(_InfrastructureFailureRunner(), task, MUTATIONS[0])

    assert outcome.classification == MutationClassification.INVALID
    assert outcome.diagnostic == "test infrastructure unavailable"


def test_mutation_score_excludes_equivalent_and_invalid_outcomes_deterministically() -> None:
    classifications = (
        MutationClassification.KILLED,
        MutationClassification.KILLED,
        MutationClassification.SURVIVED,
        MutationClassification.EQUIVALENT,
        MutationClassification.INVALID,
    )
    outcomes = [
        MutationOutcome("task", str(index), classification, 0, 0, 1, 0)
        for index, classification in enumerate(classifications)
    ]

    first = summarize_mutations(outcomes)
    second = summarize_mutations(list(outcomes))

    assert first == second
    assert first.total_generated == 5
    assert first.valid_mutants == 3
    assert first.killed == 2
    assert first.survived == 1
    assert first.equivalent == 1
    assert first.invalid == 1
    assert first.mutation_score == pytest.approx(2 / 3)


def test_historical_dataset_identities_remain_immutable() -> None:
    registry = BenchmarkDatasetRegistry.default(TaskRegistry.default())

    for version, expected in EXPECTED_DATASET_FINGERPRINTS.items():
        assert registry.get("codejudge-core", version).dataset_fingerprint == expected


def test_audit_catalog_does_not_change_benchmark_planning() -> None:
    config = load_benchmark_config(Path("benchmark-configs/real-smoke.example.yaml"))
    config = config.model_copy(
        update={"dataset": config.dataset.model_copy(update={"version": "3"})}
    )

    plan = build_plan(config, environment={})

    assert plan.dataset_version == "3"
    assert plan.dataset_fingerprint == EXPECTED_DATASET_FINGERPRINTS["3"]
    assert plan.task_count == 12
    assert plan.model_count == 2
    assert plan.planned_generations == 24
    breakdown = calculate_score(EvaluationTests(passed=1, failed=1, total=2, duration_seconds=0.1))
    assert breakdown.correctness == 50
    assert calculate_final_score(breakdown) == 50
