"""Language-runner interface used by the evaluation engine."""

from typing import Protocol

from app.evaluator.models import RunnerResult
from app.tasks.registry import RegisteredTask


class CodeRunner(Protocol):
    """Execute one candidate against a registered task's tests."""

    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult: ...
