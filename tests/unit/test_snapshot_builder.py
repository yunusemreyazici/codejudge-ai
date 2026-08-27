from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.evaluator.models import (
    ComplexityMetrics,
    EvaluationRequest,
    EvaluationResult,
    EvaluationStatus,
    Finding,
    FindingCategory,
    FindingSeverity,
    RunnerResult,
    ScoreBreakdown,
    StaticAnalysisResult,
)
from app.evaluator.models import TestResult as EvaluationTests
from app.snapshots.builder import build_evaluation_snapshot
from app.snapshots.fingerprints import source_identity
from app.snapshots.models import EvaluationDetail, ExecutionEnvironmentSnapshot
from app.tasks.registry import TaskRegistry


def test_snapshot_preserves_complete_evaluation_reasoning(correct_lru: str) -> None:
    created_at = datetime(2026, 8, 27, 10, tzinfo=UTC)
    completed_at = created_at + timedelta(seconds=1.25)
    execution_finding = Finding(
        severity=FindingSeverity.WARNING,
        category=FindingCategory.TESTING,
        message="One test failed.",
    )
    analysis_finding = Finding(
        severity=FindingSeverity.WARNING,
        category=FindingCategory.SECURITY,
        message="Security smell.",
    )
    tests = EvaluationTests(passed=7, failed=1, total=8, duration_seconds=0.8)
    result = EvaluationResult(
        task_id="lru-cache",
        status=EvaluationStatus.COMPLETED,
        score=91.5,
        tests=tests,
        score_breakdown=ScoreBreakdown(
            correctness=87.5,
            code_quality=100,
            type_safety=100,
            security=90,
            complexity=100,
        ),
        analysis=StaticAnalysisResult(
            findings=[analysis_finding],
            complexity=ComplexityMetrics(maximum=3, average=2.2, blocks=4),
        ),
        findings=[execution_finding],
    )
    runner_result = RunnerResult(
        exit_code=1,
        stdout="",
        stderr="",
        duration_seconds=0.8,
        passed=7,
        failed=1,
        total=8,
    )

    snapshot = build_evaluation_snapshot(
        request=EvaluationRequest(task_id="lru-cache", language="python", code=correct_lru),
        task=TaskRegistry.default().get("lru-cache"),
        result=result,
        runner_result=runner_result,
        created_at=created_at,
        completed_at=completed_at,
        execution=ExecutionEnvironmentSnapshot(backend="local"),
        analyzer_versions={"ruff": "0.16.4"},
        codejudge_version="0.4.0",
        scoring_policy_version="1",
        evaluation_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    expected_hash, expected_size = source_identity(correct_lru)
    assert snapshot.source_text == correct_lru
    assert snapshot.source_hash == expected_hash
    assert snapshot.source_size == expected_size
    assert snapshot.task_version == "1.0"
    assert snapshot.duration_seconds == 1.25
    assert snapshot.tests == tests
    assert snapshot.score_breakdown.security == 90
    assert snapshot.execution_findings == [execution_finding]
    assert snapshot.analysis_findings == [analysis_finding]
    assert snapshot.complexity is not None
    assert snapshot.complexity.maximum == 3
    assert snapshot.analyzer_versions == {"ruff": "0.16.4"}
    assert snapshot.scoring_policy_version == "1"
    assert len(snapshot.reproducibility_fingerprint) == 64

    detail = EvaluationDetail.from_snapshot(snapshot)
    assert detail.source_text == correct_lru
    assert detail.analysis is not None
    assert detail.analysis.findings == [analysis_finding]
