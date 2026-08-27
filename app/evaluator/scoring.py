"""Transparent deterministic scoring policy for execution and static analysis."""

from app.evaluator.models import (
    Finding,
    FindingConfidence,
    FindingSeverity,
    ScoreBreakdown,
    StaticAnalysisResult,
    TestResult,
)

CORRECTNESS_WEIGHT = 0.60
CODE_QUALITY_WEIGHT = 0.15
TYPE_SAFETY_WEIGHT = 0.10
SECURITY_WEIGHT = 0.10
COMPLEXITY_WEIGHT = 0.05

QUALITY_PENALTIES = {
    FindingSeverity.ERROR: 10.0,
    FindingSeverity.WARNING: 5.0,
    FindingSeverity.INFO: 2.0,
}
TYPE_SAFETY_PENALTIES = {
    FindingSeverity.ERROR: 8.0,
    FindingSeverity.WARNING: 4.0,
    FindingSeverity.INFO: 0.0,
}
SECURITY_SEVERITY_PENALTIES = {
    FindingSeverity.ERROR: 25.0,
    FindingSeverity.WARNING: 10.0,
    FindingSeverity.INFO: 3.0,
}
SECURITY_CONFIDENCE_MULTIPLIERS = {
    FindingConfidence.LOW: 0.50,
    FindingConfidence.MEDIUM: 0.75,
    FindingConfidence.HIGH: 1.00,
}


def calculate_score(
    tests: TestResult, analysis: StaticAnalysisResult | None = None
) -> ScoreBreakdown:
    """Calculate the complete score breakdown, or correctness alone when analysis is disabled."""
    correctness = 0.0 if tests.total == 0 else (tests.passed / tests.total) * 100
    if analysis is None:
        return ScoreBreakdown(correctness=round(correctness, 2))

    quality_findings = [finding for finding in analysis.findings if finding.category == "quality"]
    type_findings = [finding for finding in analysis.findings if finding.category == "type_safety"]
    security_findings = [finding for finding in analysis.findings if finding.category == "security"]
    return ScoreBreakdown(
        correctness=round(correctness, 2),
        code_quality=_penalty_score(quality_findings, QUALITY_PENALTIES),
        type_safety=_penalty_score(type_findings, TYPE_SAFETY_PENALTIES),
        security=_security_score(security_findings),
        complexity=_complexity_score(
            analysis.complexity.maximum,
            analyzable=analysis.complexity.analyzable,
        ),
    )


def calculate_final_score(breakdown: ScoreBreakdown) -> float:
    """Combine measured dimensions using the versioned Phase 3 weights."""
    if (
        breakdown.code_quality is None
        or breakdown.type_safety is None
        or breakdown.security is None
        or breakdown.complexity is None
    ):
        return breakdown.correctness
    weighted = (
        breakdown.correctness * CORRECTNESS_WEIGHT
        + breakdown.code_quality * CODE_QUALITY_WEIGHT
        + breakdown.type_safety * TYPE_SAFETY_WEIGHT
        + breakdown.security * SECURITY_WEIGHT
        + breakdown.complexity * COMPLEXITY_WEIGHT
    )
    return round(weighted, 2)


def _penalty_score(findings: list[Finding], penalties: dict[FindingSeverity, float]) -> float:
    penalty = sum(penalties[finding.severity] for finding in findings)
    return round(_clamp(100.0 - penalty), 2)


def _security_score(findings: list[Finding]) -> float:
    penalty = 0.0
    for finding in findings:
        confidence = finding.confidence or FindingConfidence.LOW
        penalty += (
            SECURITY_SEVERITY_PENALTIES[finding.severity]
            * SECURITY_CONFIDENCE_MULTIPLIERS[confidence]
        )
    return round(_clamp(100.0 - penalty), 2)


def _complexity_score(maximum: int, *, analyzable: bool) -> float:
    if not analyzable:
        return 0.0
    if maximum <= 5:
        return 100.0
    if maximum <= 10:
        return 90.0
    if maximum <= 15:
        return 70.0
    if maximum <= 20:
        return 50.0
    return 25.0


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))
