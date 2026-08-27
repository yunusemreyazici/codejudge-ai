"""Evaluation orchestration independent of runner implementation details."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass

from app.analysis.base import CandidateSource, StaticAnalysisProvider
from app.analysis.engine import StaticAnalysisInfrastructureError
from app.evaluator.findings import build_findings
from app.evaluator.models import (
    EvaluationRequest,
    EvaluationResult,
    EvaluationStatus,
    RunnerCapability,
    RunnerResult,
    TestResult,
)
from app.evaluator.scoring import calculate_final_score, calculate_score
from app.runners.base import CodeRunner
from app.tasks.registry import RegisteredTask, TaskRegistry

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
    """Execution or static-analysis infrastructure could not provide its service."""


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    result: EvaluationResult
    runner_result: RunnerResult
    task: RegisteredTask


class EvaluationEngine:
    """Resolve, dispatch, and score an evaluation without executing code directly."""

    def __init__(
        self,
        registry: TaskRegistry,
        runners: Mapping[str, CodeRunner],
        max_code_size: int,
        analysis_engine: StaticAnalysisProvider | None = None,
    ) -> None:
        self._registry = registry
        self._runners = runners
        self._max_code_size = max_code_size
        self._analysis_engine = analysis_engine

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        return (await self.evaluate_outcome(request)).result

    async def evaluate_outcome(self, request: EvaluationRequest) -> EvaluationOutcome:
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

        analysis = None
        if self._analysis_engine is not None:
            try:
                analysis = await self._analysis_engine.analyze(
                    CandidateSource(code=request.code, language=request.language)
                )
            except StaticAnalysisInfrastructureError as error:
                logger.error(
                    "static analysis infrastructure unavailable task_id=%s language=%s reason=%s",
                    request.task_id,
                    request.language,
                    error,
                )
                raise EvaluationInfrastructureError(str(error)) from error
        tests = TestResult(
            passed=runner_result.passed,
            failed=runner_result.failed,
            total=runner_result.total,
            duration_seconds=runner_result.duration_seconds,
            timed_out=runner_result.timed_out,
        )
        breakdown = calculate_score(tests, analysis)
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
            score=calculate_final_score(breakdown),
            tests=tests,
            score_breakdown=breakdown,
            analysis=analysis,
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
        return EvaluationOutcome(result=result, runner_result=runner_result, task=task)

    async def runner_capability(self, language: str) -> RunnerCapability:
        runner = self._runners.get(language)
        if runner is None:
            raise UnsupportedLanguageError(language)
        return await runner.check_capability()
