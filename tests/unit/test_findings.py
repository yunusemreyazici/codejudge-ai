from app.evaluator.findings import build_findings
from app.evaluator.models import FindingCategory, RunnerResult


def _runner_result(**changes: bool) -> RunnerResult:
    values: dict[str, object] = {
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "duration_seconds": 0.1,
        "passed": 1,
        "failed": 0,
        "total": 1,
    }
    values.update(changes)
    return RunnerResult.model_validate(values)


def test_oom_and_output_limit_become_resource_findings() -> None:
    findings = build_findings(
        _runner_result(oom_killed=True, output_truncated=True), timeout_seconds=5
    )

    assert [finding.category for finding in findings] == [
        FindingCategory.RESOURCE,
        FindingCategory.RESOURCE,
    ]
    assert findings[1].message == "Candidate exceeded the sandbox memory limit."


def test_sandbox_protocol_error_becomes_sandbox_finding() -> None:
    result = RunnerResult(
        exit_code=1,
        stdout="",
        stderr="",
        duration_seconds=0.1,
        passed=0,
        failed=0,
        total=0,
        sandbox_error="Sandbox report was invalid.",
    )

    findings = build_findings(result, timeout_seconds=5)

    assert findings[0].category is FindingCategory.SANDBOX
    assert findings[0].message == "Sandbox report was invalid."
