from app.evaluator.engine import (
    CodeSizeExceededError,
    EvaluationEngine,
    UnsupportedLanguageError,
)
from app.evaluator.models import EvaluationRequest, EvaluationStatus, RunnerResult
from app.tasks.registry import RegisteredTask, TaskNotFoundError, TaskRegistry


class FakeRunner:
    def __init__(self, result: RunnerResult) -> None:
        self.result = result
        self.received_code: str | None = None

    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
        self.received_code = code
        return self.result


async def test_engine_orchestrates_and_scores_runner_result() -> None:
    runner = FakeRunner(
        RunnerResult(
            exit_code=1,
            stdout="",
            stderr="",
            duration_seconds=0.25,
            passed=6,
            failed=2,
            total=8,
        )
    )
    engine = EvaluationEngine(TaskRegistry.default(), {"python": runner}, max_code_size=1000)

    result = await engine.evaluate(
        EvaluationRequest(task_id="lru-cache", language="python", code="class LRUCache: pass")
    )

    assert result.status is EvaluationStatus.COMPLETED
    assert result.score == 75.0
    assert result.tests.passed == 6
    assert result.findings[0].message == "2 tests failed."
    assert runner.received_code == "class LRUCache: pass"


async def test_engine_rejects_unknown_task() -> None:
    engine = EvaluationEngine(TaskRegistry.default(), {}, max_code_size=1000)

    try:
        await engine.evaluate(EvaluationRequest(task_id="unknown", language="python", code="pass"))
    except TaskNotFoundError as error:
        assert error.task_id == "unknown"
    else:
        raise AssertionError("TaskNotFoundError was not raised")


async def test_engine_rejects_unsupported_language() -> None:
    engine = EvaluationEngine(TaskRegistry.default(), {}, max_code_size=1000)

    try:
        await engine.evaluate(
            EvaluationRequest(task_id="lru-cache", language="javascript", code="class X {}")
        )
    except UnsupportedLanguageError as error:
        assert error.language == "javascript"
    else:
        raise AssertionError("UnsupportedLanguageError was not raised")


async def test_engine_enforces_utf8_code_size() -> None:
    engine = EvaluationEngine(TaskRegistry.default(), {}, max_code_size=3)

    try:
        await engine.evaluate(EvaluationRequest(task_id="lru-cache", language="python", code="éé"))
    except CodeSizeExceededError as error:
        assert error.actual_bytes == 4
        assert error.maximum_bytes == 3
    else:
        raise AssertionError("CodeSizeExceededError was not raised")


async def test_engine_returns_failed_result_and_finding_for_timeout() -> None:
    runner = FakeRunner(
        RunnerResult(
            exit_code=-9,
            stdout="",
            stderr="",
            duration_seconds=5.0,
            passed=0,
            failed=0,
            total=0,
            timed_out=True,
        )
    )
    engine = EvaluationEngine(TaskRegistry.default(), {"python": runner}, max_code_size=1000)

    result = await engine.evaluate(
        EvaluationRequest(task_id="lru-cache", language="python", code="while True: pass")
    )

    assert result.status is EvaluationStatus.FAILED
    assert result.score == 0
    assert result.findings[0].category == "execution"
    assert "timeout" in result.findings[0].message
