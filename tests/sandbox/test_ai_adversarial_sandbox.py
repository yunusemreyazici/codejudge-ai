from __future__ import annotations

import os

import pytest

from app.ai.adversarial import AdversarialService
from app.ai.models import AIComponentStatus
from app.ai.sandbox import DockerAdversarialSandbox
from app.core.config import Settings
from app.runners.docker_runner import DockerPythonRunner
from app.runners.factory import create_python_runner
from app.tasks.registry import TaskRegistry
from tests.ai.fakes import FakeProvider, generated_output, generated_retry_output

pytestmark = [pytest.mark.ai, pytest.mark.sandbox]


async def test_fake_generated_test_runs_against_reference_then_candidate_in_real_docker(
    correct_lru: str,
) -> None:
    runner = create_python_runner(Settings())
    assert isinstance(runner, DockerPythonRunner)
    capability = await runner.check_capability()
    if not capability.available:
        diagnostic = f"reason={capability.reason or 'unknown'} detail={capability.detail}"
        if os.getenv("CODEJUDGE_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker sandbox is required: {diagnostic}")
        pytest.skip(diagnostic)
    provider = FakeProvider()
    provider.add("adversarial", "generator-a", [generated_output()])
    service = AdversarialService(
        provider,
        DockerAdversarialSandbox(runner),
        provider_id="fake-provider",
        model="generator-a",
        max_tests=5,
        max_output_tokens=2000,
        temperature=0,
        top_p=1,
    )
    task = TaskRegistry.default().get("lru-cache")
    assert task.reference_path is not None
    result = await service.evaluate(
        payload={},
        task_id=task.specification.id,
        timeout_seconds=task.specification.timeout_seconds,
        candidate_source=correct_lru,
        reference_source=task.reference_path.read_text(encoding="utf-8"),
    )
    assert result.status is AIComponentStatus.COMPLETED
    assert result.reference_valid == 1
    assert result.candidate_passed == 1
    assert result.robustness_score == 100


async def test_adversarial_reference_discovery_is_task_generic() -> None:
    runner = create_python_runner(Settings())
    assert isinstance(runner, DockerPythonRunner)
    capability = await runner.check_capability()
    if not capability.available:
        diagnostic = f"reason={capability.reason or 'unknown'} detail={capability.detail}"
        if os.getenv("CODEJUDGE_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker sandbox is required: {diagnostic}")
        pytest.skip(diagnostic)
    provider = FakeProvider()
    provider.add("adversarial", "generator-a", [generated_retry_output()])
    service = AdversarialService(
        provider,
        DockerAdversarialSandbox(runner),
        provider_id="fake-provider",
        model="generator-a",
        max_tests=5,
        max_output_tokens=2000,
        temperature=0,
        top_p=1,
    )
    task = TaskRegistry.default().get("retry-backoff")
    assert task.reference_path is not None
    source = task.reference_path.read_text(encoding="utf-8")

    result = await service.evaluate(
        payload={},
        task_id=task.specification.id,
        timeout_seconds=task.specification.timeout_seconds,
        candidate_source=source,
        reference_source=source,
    )

    assert result.status is AIComponentStatus.COMPLETED
    assert result.reference_valid == 1
    assert result.candidate_passed == 1
    assert result.robustness_score == 100
