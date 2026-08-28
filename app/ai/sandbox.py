"""Run generated tests only through the existing restricted Docker runner."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Protocol

from app.evaluator.models import RunnerResult, Task
from app.runners.docker_runner import DockerPythonRunner
from app.tasks.registry import RegisteredTask


class AdversarialSandbox(Protocol):
    async def run(
        self,
        *,
        solution_source: str,
        test_source: str,
        task_id: str,
        timeout_seconds: float,
    ) -> RunnerResult: ...


class DockerAdversarialSandbox:
    """Creates data files on the host but executes all generated code in Docker."""

    def __init__(self, runner: DockerPythonRunner) -> None:
        self._runner = runner

    async def run(
        self,
        *,
        solution_source: str,
        test_source: str,
        task_id: str,
        timeout_seconds: float,
    ) -> RunnerResult:
        with tempfile.TemporaryDirectory(prefix="codejudge-adversarial-") as directory:
            tests_path = Path(directory) / "tests"
            tests_path.mkdir()
            (tests_path / "test_generated.py").write_text(test_source, encoding="utf-8")
            task = RegisteredTask(
                specification=Task(
                    id=f"{task_id}-adversarial",
                    title="Sandboxed generated test",
                    description="Internal generated-test execution.",
                    language="python",
                    timeout_seconds=timeout_seconds,
                    version="1",
                ),
                tests_path=tests_path,
            )
            return await self._runner.evaluate(task, solution_source)
