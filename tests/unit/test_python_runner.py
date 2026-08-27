from pathlib import Path

from app.evaluator.models import Task
from app.runners.python_runner import PythonRunner
from app.tasks.registry import RegisteredTask, TaskRegistry


async def test_runner_reports_success(correct_lru: str) -> None:
    result = await PythonRunner().evaluate(TaskRegistry.default().get("lru-cache"), correct_lru)

    assert result.exit_code == 0
    assert result.passed == 8
    assert result.failed == 0
    assert result.total == 8
    assert result.timed_out is False


async def test_runner_returns_structured_syntax_error() -> None:
    result = await PythonRunner().evaluate(
        TaskRegistry.default().get("lru-cache"), "class LRUCache(\n"
    )

    assert result.exit_code != 0
    assert result.passed == 0
    assert result.failed > 0
    assert result.total == result.failed
    assert "SyntaxError" in result.stdout + result.stderr


async def test_runner_handles_timeout(tmp_path: Path) -> None:
    tests_path = tmp_path / "tests"
    tests_path.mkdir()
    (tests_path / "test_candidate.py").write_text(
        "from solution import value\n\ndef test_value(): assert value == 1\n"
    )
    task = RegisteredTask(
        specification=Task(
            id="timeout",
            title="Timeout",
            description="Timeout test",
            language="python",
            timeout_seconds=0.15,
        ),
        tests_path=tests_path,
    )

    result = await PythonRunner().evaluate(task, "while True:\n    pass\n")

    assert result.timed_out is True
    assert result.exit_code is not None
    assert result.total == 0
