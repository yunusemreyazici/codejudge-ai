from __future__ import annotations

import os

import pytest
import pytest_asyncio

from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.core.config import Settings
from app.runners.docker_runner import DockerPythonRunner
from app.runners.factory import create_python_runner
from app.tasks.registry import TaskRegistry
from tests.tasks.mutation_audit import (
    MutationClassification,
    MutationDefinition,
    execute_dataset_mutation,
)
from tests.tasks.mutation_catalog import MUTATIONS_BY_TASK

pytestmark = pytest.mark.sandbox
TASKS = TaskRegistry.default()
DATASETS = BenchmarkDatasetRegistry.default(TASKS)
CORE_V3 = DATASETS.get("codejudge-core", "3")

REPRESENTATIVE_MUTATIONS = tuple(
    next(
        mutation
        for mutation in mutations
        if mutation.equivalent_reason is None and mutation.survivor_reason is None
    )
    for mutations in MUTATIONS_BY_TASK.values()
)


@pytest_asyncio.fixture(scope="module")
async def mutation_runner() -> DockerPythonRunner:
    runner = create_python_runner(Settings())
    assert isinstance(runner, DockerPythonRunner)
    capability = await runner.check_capability()
    if not capability.available:
        diagnostic = f"reason={capability.reason or 'unknown'} detail={capability.detail}"
        if os.getenv("CODEJUDGE_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker sandbox is required: {diagnostic}")
        pytest.skip(diagnostic)
    return runner


@pytest.mark.parametrize(
    "mutation",
    REPRESENTATIVE_MUTATIONS,
    ids=lambda mutation: mutation.task_id,
)
async def test_representative_mutant_uses_private_safe_official_harness(
    mutation_runner: DockerPythonRunner,
    mutation: MutationDefinition,
) -> None:
    outcome = await execute_dataset_mutation(mutation_runner, DATASETS, CORE_V3, mutation)

    assert outcome.classification == MutationClassification.KILLED
    assert outcome.total > 0
    assert outcome.diagnostic is None
