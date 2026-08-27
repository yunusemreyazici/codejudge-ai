import pytest

from app.evaluator.models import (
    ComplexityMetrics,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    StaticAnalysisResult,
)
from app.evaluator.models import TestResult as EvaluationTests
from app.evaluator.scoring import calculate_final_score, calculate_score


def _tests(passed: int = 8, total: int = 8) -> EvaluationTests:
    return EvaluationTests(
        passed=passed,
        failed=total - passed,
        total=total,
        duration_seconds=0.1,
    )


def _analysis(
    findings: list[Finding] | None = None,
    *,
    maximum: int = 1,
    analyzable: bool = True,
) -> StaticAnalysisResult:
    return StaticAnalysisResult(
        findings=findings or [],
        complexity=ComplexityMetrics(
            maximum=maximum,
            average=float(maximum),
            blocks=1 if analyzable else 0,
            analyzable=analyzable,
        ),
    )


def test_perfect_result_scores_100() -> None:
    breakdown = calculate_score(_tests(), _analysis())

    assert breakdown.model_dump() == {
        "correctness": 100.0,
        "code_quality": 100.0,
        "type_safety": 100.0,
        "security": 100.0,
        "complexity": 100.0,
    }
    assert calculate_final_score(breakdown) == 100.0


def test_weighted_score_keeps_correctness_at_60_percent() -> None:
    breakdown = calculate_score(_tests(passed=4), _analysis())

    assert breakdown.correctness == 50
    assert calculate_final_score(breakdown) == 70


def test_quality_and_type_penalties_are_deterministic() -> None:
    findings = [
        Finding(
            severity=FindingSeverity.ERROR,
            category=FindingCategory.QUALITY,
            message="quality",
        ),
        Finding(
            severity=FindingSeverity.WARNING,
            category=FindingCategory.QUALITY,
            message="quality",
        ),
        Finding(
            severity=FindingSeverity.ERROR,
            category=FindingCategory.TYPE_SAFETY,
            message="typing",
        ),
    ]

    breakdown = calculate_score(_tests(), _analysis(findings))

    assert breakdown.code_quality == 85
    assert breakdown.type_safety == 92


def test_repeated_penalties_clamp_at_zero() -> None:
    findings = [
        Finding(
            severity=FindingSeverity.ERROR,
            category=FindingCategory.QUALITY,
            message="quality",
        )
        for _ in range(20)
    ]

    assert calculate_score(_tests(), _analysis(findings)).code_quality == 0


def test_security_penalty_uses_severity_and_confidence() -> None:
    high_confidence = Finding(
        severity=FindingSeverity.ERROR,
        category=FindingCategory.SECURITY,
        message="security",
        confidence=FindingConfidence.HIGH,
    )
    low_confidence = Finding(
        severity=FindingSeverity.ERROR,
        category=FindingCategory.SECURITY,
        message="security",
        confidence=FindingConfidence.LOW,
    )

    assert calculate_score(_tests(), _analysis([high_confidence])).security == 75
    assert calculate_score(_tests(), _analysis([low_confidence])).security == 87.5


@pytest.mark.parametrize(
    ("maximum", "expected"),
    [(1, 100), (5, 100), (6, 90), (11, 70), (16, 50), (21, 25)],
)
def test_complexity_thresholds(maximum: int, expected: float) -> None:
    assert calculate_score(_tests(), _analysis(maximum=maximum)).complexity == expected


def test_unanalyzable_complexity_does_not_receive_100() -> None:
    assert calculate_score(_tests(), _analysis(analyzable=False)).complexity == 0


def test_analysis_disabled_preserves_legacy_correctness_score() -> None:
    breakdown = calculate_score(_tests(passed=1, total=3))

    assert breakdown.correctness == 33.33
    assert breakdown.code_quality is None
    assert calculate_final_score(breakdown) == 33.33
