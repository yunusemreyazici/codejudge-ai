"""Typed lifecycle models for durable evaluation work."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.ai.models import AIIdentity, AIStatus
from app.evaluator.models import ScoreBreakdown
from app.snapshots.models import ExecutionEnvironmentSnapshot


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_JOB_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED})


class EvaluationJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    evaluation_id: UUID
    created_at: datetime
    updated_at: datetime
    task_id: str
    task_version: str
    task_fingerprint: str
    tests_fingerprint: str
    language: str
    source_text: str
    source_hash: str
    source_size: int = Field(gt=0)
    request_fingerprint: str
    idempotency_key: str | None = None
    status: JobStatus
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(gt=0)
    queued_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    next_attempt_at: datetime | None = None
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    last_error_category: str | None = None
    last_error_code: str | None = None
    snapshot_created: bool = False
    expected_execution: ExecutionEnvironmentSnapshot
    expected_analyzer_versions: dict[str, str]
    expected_scoring_policy_version: str
    expected_codejudge_version: str
    expected_ai_identity: AIIdentity


class JobError(BaseModel):
    category: str
    code: str


class EvaluationAccepted(BaseModel):
    evaluation_id: UUID
    status: JobStatus
    created_at: datetime
    status_url: str


class EvaluationJobDetail(BaseModel):
    evaluation_id: UUID
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    task_id: str
    task_version: str
    language: str
    source_hash: str
    attempt_count: int
    max_attempts: int
    queued_at: datetime
    started_at: datetime | None = None
    failed_at: datetime | None = None
    next_attempt_at: datetime | None = None
    error: JobError | None = None

    @classmethod
    def from_job(cls, job: EvaluationJob) -> EvaluationJobDetail:
        error = None
        if job.last_error_category is not None and job.last_error_code is not None:
            error = JobError(category=job.last_error_category, code=job.last_error_code)
        return cls(
            evaluation_id=job.evaluation_id,
            status=job.status,
            created_at=job.created_at,
            updated_at=job.updated_at,
            task_id=job.task_id,
            task_version=job.task_version,
            language=job.language,
            source_hash=job.source_hash,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            queued_at=job.queued_at,
            started_at=job.started_at,
            failed_at=job.failed_at,
            next_attempt_at=job.next_attempt_at,
            error=error,
        )


class EvaluationJobSummary(BaseModel):
    evaluation_id: UUID
    created_at: datetime
    updated_at: datetime
    task_id: str
    task_version: str
    language: str
    source_hash: str
    status: JobStatus
    attempt_count: int
    score: float | None = Field(default=None, ge=0, le=100)
    score_breakdown: ScoreBreakdown | None = None
    ai_status: AIStatus | None = None
    ai_score: float | None = Field(default=None, ge=0, le=100)


class OutboxEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID
    aggregate_id: UUID
    event_type: str
    created_at: datetime
    published_at: datetime | None = None
    attempt_count: int = Field(ge=0)
    next_attempt_at: datetime


class JobClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    job: EvaluationJob
    worker_id: str
