import pytest

from app.evaluator.models import TestResult as EvaluationTests
from app.evaluator.scoring import calculate_score


@pytest.mark.parametrize(
    ("passed", "failed", "total", "expected"),
    [
        (9, 1, 10, 90.0),
        (1, 2, 3, 33.33),
        (0, 0, 0, 0.0),
        (8, 0, 8, 100.0),
    ],
)
def test_calculate_score(passed: int, failed: int, total: int, expected: float) -> None:
    tests = EvaluationTests(
        passed=passed,
        failed=failed,
        total=total,
        duration_seconds=0.1,
    )
    assert calculate_score(tests).correctness == expected
