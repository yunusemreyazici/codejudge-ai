"""Deterministic score calculation."""

from app.evaluator.models import ScoreBreakdown, TestResult


def calculate_score(tests: TestResult) -> ScoreBreakdown:
    """Calculate correctness as the percentage of tests passed."""
    correctness = 0.0 if tests.total == 0 else (tests.passed / tests.total) * 100
    return ScoreBreakdown(correctness=round(correctness, 2))
