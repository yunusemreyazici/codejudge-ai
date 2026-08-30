from __future__ import annotations

import os

import pytest
import pytest_asyncio

from app.core.config import Settings
from app.runners.docker_runner import DockerPythonRunner
from app.runners.factory import create_python_runner
from app.runners.trusted_harness import OFFICIAL_CASES
from app.tasks.registry import TaskRegistry
from tests.tasks.candidates import INCORRECT_CANDIDATES

pytestmark = pytest.mark.sandbox
TRUSTED_TASK_IDS = tuple(task.id for task in TaskRegistry.default().list())


@pytest_asyncio.fixture(scope="module")
async def portfolio_runner() -> DockerPythonRunner:
    runner = create_python_runner(Settings())
    assert isinstance(runner, DockerPythonRunner)
    capability = await runner.check_capability()
    if not capability.available:
        diagnostic = f"reason={capability.reason or 'unknown'} detail={capability.detail}"
        if os.getenv("CODEJUDGE_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker sandbox is required: {diagnostic}")
        pytest.skip(diagnostic)
    return runner


@pytest.mark.parametrize("task_id", TRUSTED_TASK_IDS)
async def test_trusted_reference_passes_in_real_docker(
    portfolio_runner: DockerPythonRunner, task_id: str
) -> None:
    task = TaskRegistry.default().get(task_id)
    assert task.reference_path is not None

    result = await portfolio_runner.evaluate(task, task.reference_path.read_text(encoding="utf-8"))

    assert result.infrastructure_error is None
    assert result.sandbox_error is None
    assert result.timed_out is False
    assert result.oom_killed is False
    assert result.failed == 0
    assert result.passed == result.total == len(OFFICIAL_CASES[task_id])


@pytest.mark.parametrize("task_id", tuple(INCORRECT_CANDIDATES))
async def test_incorrect_candidate_fails_in_real_docker(
    portfolio_runner: DockerPythonRunner, task_id: str
) -> None:
    task = TaskRegistry.default().get(task_id)

    result = await portfolio_runner.evaluate(task, INCORRECT_CANDIDATES[task_id])

    assert result.infrastructure_error is None
    assert result.sandbox_error is None
    assert result.timed_out is False
    assert result.oom_killed is False
    assert result.total > 0
    assert result.failed > 0
