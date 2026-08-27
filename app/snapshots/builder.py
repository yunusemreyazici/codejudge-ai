"""Construct complete immutable evaluation snapshots from domain results."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from app.evaluator.models import EvaluationRequest, EvaluationResult, RunnerResult
from app.snapshots.fingerprints import (
    reproducibility_fingerprint,
    source_identity,
    task_fingerprint,
    tests_fingerprint,
)
from app.snapshots.models import EvaluationSnapshot, ExecutionEnvironmentSnapshot
from app.tasks.registry import RegisteredTask


def build_evaluation_snapshot(
    *,
    request: EvaluationRequest,
    task: RegisteredTask,
    result: EvaluationResult,
    runner_result: RunnerResult,
    created_at: datetime,
    completed_at: datetime,
    execution: ExecutionEnvironmentSnapshot,
    analyzer_versions: dict[str, str],
    codejudge_version: str,
    scoring_policy_version: str,
    evaluation_id: UUID | None = None,
) -> EvaluationSnapshot:
    source_hash, source_size = source_identity(request.code)
    test_hash = tests_fingerprint(task)
    task_hash = task_fingerprint(task, test_hash)
    reproducibility_hash = reproducibility_fingerprint(
        source_hash=source_hash,
        task_hash=task_hash,
        tests_hash=test_hash,
        analyzer_versions=analyzer_versions,
        scoring_policy_version=scoring_policy_version,
        execution=execution,
        codejudge_version=codejudge_version,
    )
    return EvaluationSnapshot(
        evaluation_id=evaluation_id or uuid4(),
        created_at=created_at,
        completed_at=completed_at,
        duration_seconds=max(0.0, (completed_at - created_at).total_seconds()),
        task_id=task.specification.id,
        task_version=task.specification.version,
        task_fingerprint=task_hash,
        tests_fingerprint=test_hash,
        language=request.language,
        source_text=request.code,
        source_hash=source_hash,
        source_size=source_size,
        status=result.status,
        execution=execution,
        codejudge_version=codejudge_version,
        scoring_policy_version=scoring_policy_version,
        analyzer_versions=dict(analyzer_versions),
        tests=result.tests,
        oom_killed=runner_result.oom_killed,
        score_breakdown=result.score_breakdown,
        final_score=result.score,
        complexity=None if result.analysis is None else result.analysis.complexity,
        execution_findings=result.findings,
        analysis_findings=[] if result.analysis is None else result.analysis.findings,
        reproducibility_fingerprint=reproducibility_hash,
    )
