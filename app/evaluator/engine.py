"""Evaluation orchestration independent of runner implementation details."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping

from app.evaluator.findings import build_findings
from app.evaluator.models import (
    EvaluationRequest,
    EvaluationResult,
    EvaluationStatus,
    RunnerCapability,
    TestResult,
)
from app.evaluator.scoring import calculate_score
from app.runners.base import CodeRunner
from app.tasks.registry import TaskRegistry

logger = logging.getLogger(__name__)


class UnsupportedLanguageError(ValueError):
    def __init__(self, language: str) -> None:
        super().__init__(f"Unsupported language: {language}")
        self.language = language


class CodeSizeExceededError(ValueError):
    def __init__(self, actual_bytes: int, maximum_bytes: int) -> None:
        super().__init__(f"Code is {actual_bytes} bytes; maximum is {maximum_bytes} bytes")
        self.actual_bytes = actual_bytes
        self.maximum_bytes = maximum_bytes


class EvaluationInfrastructureError(RuntimeError):
    """The configured runner could not provide its execution service."""


class EvaluationEngine:
    """Resolve, dispatch, and score an evaluation without executing code directly."""

    def __init__(
        self,
        registry: TaskRegistry,
        runners: Mapping[str, CodeRunner],
        max_code_size: int,
    ) -> None:
        self._registry = registry
        self._runners = runners
        self._max_code_size = max_code_size

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        started_at = time.monotonic()
        code_size = len(request.code.encode("utf-8"))
        if code_size > self._max_code_size:
            raise CodeSizeExceededError(code_size, self._max_code_size)

        task = self._registry.get(request.task_id)
        if request.language != task.specification.language:
            raise UnsupportedLanguageError(request.language)
        runner = self._runners.get(request.language)
        if runner is None:
            raise UnsupportedLanguageError(request.language)

        logger.info("evaluation started task_id=%s language=%s", request.task_id, request.language)
        runner_result = await runner.evaluate(task, request.code)
        if runner_result.infrastructure_error is not None:
            logger.error(
                "evaluation infrastructure unavailable task_id=%s language=%s reason=%s",
                request.task_id,
                request.language,
                runner_result.infrastructure_error,
            )
            raise EvaluationInfrastructureError(runner_result.infrastructure_error)
        tests = TestResult(
            passed=runner_result.passed,
            failed=runner_result.failed,
            total=runner_result.total,
            duration_seconds=runner_result.duration_seconds,
            timed_out=runner_result.timed_out,
        )
        breakdown = calculate_score(tests)
        status = (
            EvaluationStatus.FAILED
            if (
                runner_result.timed_out
                or runner_result.oom_killed
                or runner_result.sandbox_error is not None
            )
            else EvaluationStatus.COMPLETED
        )
        result = EvaluationResult(
            task_id=task.specification.id,
            status=status,
            score=breakdown.correctness,
            tests=tests,
            score_breakdown=breakdown,
            findings=build_findings(runner_result, task.specification.timeout_seconds),
        )
        logger.info(
            "evaluation finished task_id=%s status=%s passed=%d failed=%d duration=%.3f",
            request.task_id,
            result.status,
            tests.passed,
            tests.failed,
            time.monotonic() - started_at,
        )
        return result

    async def runner_capability(self, language: str) -> RunnerCapability:
        runner = self._runners.get(language)
        if runner is None:
            raise UnsupportedLanguageError(language)
        return await runner.check_capability()
