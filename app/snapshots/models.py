"""Typed immutable snapshot and public history models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.ai.models import AIAssessment, AIStatus
from app.evaluator.models import (
    ComplexityMetrics,
    EvaluationResult,
    EvaluationStatus,
    Finding,
    ScoreBreakdown,
    StaticAnalysisResult,
    TestResult,
)


class ExecutionEnvironmentSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    backend: str = Field(min_length=1)
    sandbox_image: str | None = None
    sandbox_image_id: str | None = None


class EvaluationSnapshot(BaseModel):
    """Complete append-only record produced by one trustworthy evaluation."""

    model_config = ConfigDict(frozen=True)

    evaluation_id: UUID
    created_at: datetime
    completed_at: datetime
    duration_seconds: float = Field(ge=0)
    task_id: str
    task_version: str
    task_fingerprint: str = Field(min_length=64, max_length=64)
    tests_fingerprint: str = Field(min_length=64, max_length=64)
    language: str
    source_text: str
    source_hash: str = Field(min_length=64, max_length=64)
    source_size: int = Field(gt=0)
    status: EvaluationStatus
    execution: ExecutionEnvironmentSnapshot
    codejudge_version: str
    scoring_policy_version: str
    analyzer_versions: dict[str, str]
    tests: TestResult
    oom_killed: bool
    score_breakdown: ScoreBreakdown
    final_score: float = Field(ge=0, le=100)
    complexity: ComplexityMetrics | None = None
    execution_findings: list[Finding] = Field(default_factory=list)
    analysis_findings: list[Finding] = Field(default_factory=list)
    reproducibility_fingerprint: str = Field(min_length=64, max_length=64)
    ai_assessment: AIAssessment | None = None


class EvaluationDetail(EvaluationResult):
    evaluation_id: UUID
    created_at: datetime
    completed_at: datetime
    duration_seconds: float = Field(ge=0)
    task_version: str
    task_fingerprint: str
    tests_fingerprint: str
    language: str
    source_text: str
    source_hash: str
    source_size: int = Field(gt=0)
    execution: ExecutionEnvironmentSnapshot
    codejudge_version: str
    scoring_policy_version: str
    analyzer_versions: dict[str, str]
    oom_killed: bool
    reproducibility_fingerprint: str

    @classmethod
    def from_snapshot(cls, snapshot: EvaluationSnapshot) -> EvaluationDetail:
        analysis = None
        if snapshot.complexity is not None:
            analysis = StaticAnalysisResult(
                findings=snapshot.analysis_findings,
                complexity=snapshot.complexity,
            )
        return cls(
            evaluation_id=snapshot.evaluation_id,
            created_at=snapshot.created_at,
            completed_at=snapshot.completed_at,
            duration_seconds=snapshot.duration_seconds,
            task_id=snapshot.task_id,
            task_version=snapshot.task_version,
            task_fingerprint=snapshot.task_fingerprint,
            tests_fingerprint=snapshot.tests_fingerprint,
            language=snapshot.language,
            source_text=snapshot.source_text,
            source_hash=snapshot.source_hash,
            source_size=snapshot.source_size,
            status=snapshot.status,
            execution=snapshot.execution,
            codejudge_version=snapshot.codejudge_version,
            scoring_policy_version=snapshot.scoring_policy_version,
            analyzer_versions=snapshot.analyzer_versions,
            oom_killed=snapshot.oom_killed,
            score=snapshot.final_score,
            tests=snapshot.tests,
            score_breakdown=snapshot.score_breakdown,
            analysis=analysis,
            findings=snapshot.execution_findings,
            reproducibility_fingerprint=snapshot.reproducibility_fingerprint,
            ai_assessment=snapshot.ai_assessment,
        )


class EvaluationSummary(BaseModel):
    evaluation_id: UUID
    created_at: datetime
    task_id: str
    task_version: str
    language: str
    source_hash: str
    status: EvaluationStatus
    score: float = Field(ge=0, le=100)
    score_breakdown: ScoreBreakdown
    ai_status: AIStatus | None = None
    ai_score: float | None = Field(default=None, ge=0, le=100)
