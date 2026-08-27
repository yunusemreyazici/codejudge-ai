from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.evaluator.models import (
    ComplexityMetrics,
    EvaluationRequest,
    EvaluationResult,
    EvaluationStatus,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    RunnerResult,
    ScoreBreakdown,
    StaticAnalysisResult,
    TestResult,
)
from app.jobs.integrity import request_fingerprint
from app.jobs.models import EvaluationJob, JobStatus
from app.snapshots.builder import build_evaluation_snapshot
from app.snapshots.fingerprints import source_identity, task_fingerprint, tests_fingerprint
from app.snapshots.models import EvaluationSnapshot, ExecutionEnvironmentSnapshot
from app.tasks.registry import TaskRegistry


def job_fixture(
    *,
    source: str = "class LRUCache: pass\n",
    created_at: datetime | None = None,
    evaluation_id: UUID | None = None,
    idempotency_key: str | None = None,
    max_attempts: int = 3,
) -> EvaluationJob:
    now = created_at or datetime(2026, 8, 27, 10, tzinfo=UTC)
    request = EvaluationRequest(task_id="lru-cache", language="python", code=source)
    task = TaskRegistry.default().get("lru-cache")
    source_hash, source_size = source_identity(source)
    tests_hash = tests_fingerprint(task)
    return EvaluationJob(
        evaluation_id=evaluation_id or uuid4(),
        created_at=now,
        updated_at=now,
        task_id=request.task_id,
        task_version=task.specification.version,
        task_fingerprint=task_fingerprint(task, tests_hash),
        tests_fingerprint=tests_hash,
        language=request.language,
        source_text=source,
        source_hash=source_hash,
        source_size=source_size,
        request_fingerprint=request_fingerprint(request),
        idempotency_key=idempotency_key,
        status=JobStatus.QUEUED,
        attempt_count=0,
        max_attempts=max_attempts,
        queued_at=now,
        expected_execution=ExecutionEnvironmentSnapshot(backend="local"),
        expected_analyzer_versions={},
        expected_scoring_policy_version="1",
        expected_codejudge_version="0.5.0",
    )


def snapshot_fixture(
    *,
    source: str = "class LRUCache: pass\n",
    created_at: datetime | None = None,
    evaluation_id: UUID | None = None,
) -> EvaluationSnapshot:
    started = created_at or datetime(2026, 8, 27, 10, tzinfo=UTC)
    tests = TestResult(passed=6, failed=2, total=8, duration_seconds=0.4)
    analysis_finding = Finding(
        severity=FindingSeverity.WARNING,
        category=FindingCategory.SECURITY,
        tool="bandit",
        code="B307",
        message="Use of possibly insecure function.",
        line=4,
        column=5,
        confidence=FindingConfidence.HIGH,
    )
    execution_finding = Finding(
        severity=FindingSeverity.WARNING,
        category=FindingCategory.TESTING,
        message="2 tests failed.",
    )
    result = EvaluationResult(
        task_id="lru-cache",
        status=EvaluationStatus.COMPLETED,
        score=82.75,
        tests=tests,
        score_breakdown=ScoreBreakdown(
            correctness=75,
            code_quality=95,
            type_safety=100,
            security=90,
            complexity=90,
        ),
        analysis=StaticAnalysisResult(
            findings=[analysis_finding],
            complexity=ComplexityMetrics(maximum=7, average=4.2, blocks=4),
        ),
        findings=[execution_finding],
    )
    runner_result = RunnerResult(
        exit_code=1,
        stdout="",
        stderr="",
        duration_seconds=0.4,
        passed=6,
        failed=2,
        total=8,
    )
    return build_evaluation_snapshot(
        request=EvaluationRequest(task_id="lru-cache", language="python", code=source),
        task=TaskRegistry.default().get("lru-cache"),
        result=result,
        runner_result=runner_result,
        created_at=started,
        completed_at=started + timedelta(seconds=0.75),
        execution=ExecutionEnvironmentSnapshot(
            backend="docker",
            sandbox_image="codejudge-python-sandbox:phase2",
            sandbox_image_id="sha256:test-image",
        ),
        analyzer_versions={
            "ruff": "0.16.4",
            "mypy": "1.20.2",
            "bandit": "1.9.4",
            "radon": "6.0.1",
        },
        codejudge_version="0.4.0",
        scoring_policy_version="1",
        evaluation_id=evaluation_id or uuid4(),
    )
