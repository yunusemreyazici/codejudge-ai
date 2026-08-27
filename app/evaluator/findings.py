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
        enforced_timeout = result.enforced_timeout_seconds or timeout_seconds
        findings.append(
            Finding(
                severity=FindingSeverity.ERROR,
                category=FindingCategory.EXECUTION,
                message=(
                    f"Candidate code exceeded the {enforced_timeout:g} second execution timeout."
                ),
            )
        )
        _append_output_finding(findings, result)
        return findings

    if result.oom_killed:
        findings.append(
            Finding(
                severity=FindingSeverity.ERROR,
                category=FindingCategory.RESOURCE,
                message="Candidate exceeded the sandbox memory limit.",
            )
        )
        _append_output_finding(findings, result)
        return findings

    if result.sandbox_error is not None:
        findings.append(
            Finding(
                severity=FindingSeverity.ERROR,
                category=FindingCategory.SANDBOX,
                message=result.sandbox_error,
            )
        )
        _append_output_finding(findings, result)
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
    if result.syntax_error or "SyntaxError" in output:
        findings.append(
            Finding(
                severity=FindingSeverity.ERROR,
                category=FindingCategory.EXECUTION,
                message="Candidate code contains a syntax error.",
            )
        )
    elif result.import_error or "ImportError" in output or "ModuleNotFoundError" in output:
        findings.append(
            Finding(
                severity=FindingSeverity.ERROR,
                category=FindingCategory.EXECUTION,
                message="Candidate code could not be imported by the test suite.",
            )
        )

    _append_output_finding(findings, result)

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


def _append_output_finding(findings: list[Finding], result: RunnerResult) -> None:
    if result.output_truncated:
        findings.append(
            Finding(
                severity=FindingSeverity.WARNING,
                category=FindingCategory.RESOURCE,
                message=(
                    "Candidate output exceeded the configured capture limit and was truncated."
                ),
            )
        )
