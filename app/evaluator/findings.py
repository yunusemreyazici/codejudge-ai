"""Translate runner outcomes into stable, user-facing findings."""

from app.evaluator.models import (
    Finding,
    FindingCategory,
    FindingSeverity,
    RunnerResult,
)


def build_findings(result: RunnerResult, timeout_seconds: float) -> list[Finding]:
    findings: list[Finding] = []

    if result.timed_out:
        findings.append(
            Finding(
                severity=FindingSeverity.ERROR,
                category=FindingCategory.EXECUTION,
                message=(
                    f"Candidate code exceeded the {timeout_seconds:g} second execution timeout."
                ),
            )
        )
        return findings

    if result.infrastructure_error is not None:
        findings.append(
            Finding(
                severity=FindingSeverity.ERROR,
                category=FindingCategory.EXECUTION,
                message="The evaluation process could not be completed.",
            )
        )
        return findings

    output = f"{result.stdout}\n{result.stderr}"
    if "SyntaxError" in output:
        findings.append(
            Finding(
                severity=FindingSeverity.ERROR,
                category=FindingCategory.EXECUTION,
                message="Candidate code contains a syntax error.",
            )
        )
    elif "ImportError" in output or "ModuleNotFoundError" in output:
        findings.append(
            Finding(
                severity=FindingSeverity.ERROR,
                category=FindingCategory.EXECUTION,
                message="Candidate code could not be imported by the test suite.",
            )
        )

    if result.failed > 0:
        suffix = "test failed" if result.failed == 1 else "tests failed"
        findings.append(
            Finding(
                severity=FindingSeverity.WARNING,
                category=FindingCategory.TESTING,
                message=f"{result.failed} {suffix}.",
            )
        )
    elif result.total == 0:
        findings.append(
            Finding(
                severity=FindingSeverity.ERROR,
                category=FindingCategory.TESTING,
                message="No tests were executed; correctness could not be measured.",
            )
        )

    return findings
