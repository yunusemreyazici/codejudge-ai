"""Typed SQLAlchemy mappings for append-only evaluation snapshots."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EvaluationRecord(Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        CheckConstraint("source_size > 0", name="ck_evaluations_source_size_positive"),
        CheckConstraint("length(source_hash) = 64", name="ck_evaluations_source_hash_length"),
        CheckConstraint(
            "length(task_fingerprint) = 64",
            name="ck_evaluations_task_fingerprint_length",
        ),
        CheckConstraint(
            "length(tests_fingerprint) = 64",
            name="ck_evaluations_tests_fingerprint_length",
        ),
        CheckConstraint(
            "length(reproducibility_fingerprint) = 64",
            name="ck_evaluations_reproducibility_fingerprint_length",
        ),
        CheckConstraint(
            "tests_passed >= 0 AND tests_failed >= 0 AND tests_total >= 0",
            name="ck_evaluations_test_counts_nonnegative",
        ),
        CheckConstraint(
            "tests_passed + tests_failed = tests_total",
            name="ck_evaluations_test_counts_total",
        ),
        CheckConstraint("duration_seconds >= 0", name="ck_evaluations_duration_nonnegative"),
        CheckConstraint(
            "correctness_score BETWEEN 0 AND 100",
            name="ck_evaluations_correctness_score",
        ),
        CheckConstraint("final_score BETWEEN 0 AND 100", name="ck_evaluations_final_score"),
        CheckConstraint(
            "ai_status IS NULL OR ai_status IN "
            "('disabled', 'completed', 'partial', 'unavailable', 'disputed', 'skipped')",
            name="ck_evaluations_ai_status",
        ),
        CheckConstraint(
            "ai_score IS NULL OR ai_score BETWEEN 0 AND 100",
            name="ck_evaluations_ai_score",
        ),
        CheckConstraint(
            "judge_score IS NULL OR judge_score BETWEEN 0 AND 100",
            name="ck_evaluations_judge_score",
        ),
        CheckConstraint(
            "adversarial_robustness IS NULL OR adversarial_robustness BETWEEN 0 AND 100",
            name="ck_evaluations_adversarial_robustness",
        ),
        CheckConstraint(
            "ai_reproducibility_fingerprint IS NULL OR length(ai_reproducibility_fingerprint) = 64",
            name="ck_evaluations_ai_fingerprint",
        ),
        CheckConstraint(
            "code_quality_score IS NULL OR code_quality_score BETWEEN 0 AND 100",
            name="ck_evaluations_code_quality_score",
        ),
        CheckConstraint(
            "type_safety_score IS NULL OR type_safety_score BETWEEN 0 AND 100",
            name="ck_evaluations_type_safety_score",
        ),
        CheckConstraint(
            "security_score IS NULL OR security_score BETWEEN 0 AND 100",
            name="ck_evaluations_security_score",
        ),
        CheckConstraint(
            "complexity_score IS NULL OR complexity_score BETWEEN 0 AND 100",
            name="ck_evaluations_complexity_score",
        ),
        Index("ix_evaluations_created_at", "created_at"),
        Index("ix_evaluations_task_id", "task_id"),
        Index("ix_evaluations_source_hash", "source_hash"),
        Index("ix_evaluations_final_score", "final_score"),
        Index("ix_evaluations_ai_status", "ai_status"),
    )

    evaluation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)

    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    task_version: Mapped[str] = mapped_column(Text, nullable=False)
    task_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    tests_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)

    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    execution_backend: Mapped[str] = mapped_column(String(32), nullable=False)
    sandbox_image: Mapped[str | None] = mapped_column(Text)
    sandbox_image_id: Mapped[str | None] = mapped_column(Text)
    codejudge_version: Mapped[str] = mapped_column(Text, nullable=False)
    scoring_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    analyzer_versions: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)

    tests_passed: Mapped[int] = mapped_column(Integer, nullable=False)
    tests_failed: Mapped[int] = mapped_column(Integer, nullable=False)
    tests_total: Mapped[int] = mapped_column(Integer, nullable=False)
    test_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    timed_out: Mapped[bool] = mapped_column(Boolean, nullable=False)
    oom_killed: Mapped[bool] = mapped_column(Boolean, nullable=False)

    correctness_score: Mapped[float] = mapped_column(Float, nullable=False)
    code_quality_score: Mapped[float | None] = mapped_column(Float)
    type_safety_score: Mapped[float | None] = mapped_column(Float)
    security_score: Mapped[float | None] = mapped_column(Float)
    complexity_score: Mapped[float | None] = mapped_column(Float)
    final_score: Mapped[float] = mapped_column(Float, nullable=False)

    complexity: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    execution_findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    analysis_findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    reproducibility_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    ai_status: Mapped[str | None] = mapped_column(String(32))
    ai_reason: Mapped[str | None] = mapped_column(String(128))
    ai_score: Mapped[float | None] = mapped_column(Float)
    judge_score: Mapped[float | None] = mapped_column(Float)
    adversarial_robustness: Mapped[float | None] = mapped_column(Float)
    ai_reproducibility_fingerprint: Mapped[str | None] = mapped_column(String(64))
    ai_judge_results: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ai_adversarial_results: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ai_provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class EvaluationJobRecord(Base):
    """Mutable durable lifecycle; terminal evaluation snapshots remain separate and immutable."""

    __tablename__ = "evaluation_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'completed', 'failed')",
            name="ck_evaluation_jobs_status",
        ),
        CheckConstraint("source_size > 0", name="ck_evaluation_jobs_source_size_positive"),
        CheckConstraint("length(source_hash) = 64", name="ck_evaluation_jobs_source_hash_length"),
        CheckConstraint(
            "length(task_fingerprint) = 64",
            name="ck_evaluation_jobs_task_fingerprint_length",
        ),
        CheckConstraint(
            "length(tests_fingerprint) = 64",
            name="ck_evaluation_jobs_tests_fingerprint_length",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_evaluation_jobs_request_fingerprint_length",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_evaluation_jobs_attempts",
        ),
        CheckConstraint(
            "(status = 'completed' AND snapshot_created) OR "
            "(status <> 'completed' AND NOT snapshot_created)",
            name="ck_evaluation_jobs_snapshot_state",
        ),
        Index("ix_evaluation_jobs_created_at", "created_at"),
        Index("ix_evaluation_jobs_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_evaluation_jobs_lease_expires_at", "lease_expires_at"),
        Index("ix_evaluation_jobs_task_id", "task_id"),
    )

    evaluation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    task_version: Mapped[str] = mapped_column(Text, nullable=False)
    task_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    tests_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_size: Mapped[int] = mapped_column(Integer, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_category: Mapped[str | None] = mapped_column(String(64))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    snapshot_created: Mapped[bool] = mapped_column(Boolean, nullable=False)
    expected_execution: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expected_analyzer_versions: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    expected_scoring_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    expected_codejudge_version: Mapped[str] = mapped_column(Text, nullable=False)
    expected_ai_identity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class OutboxEventRecord(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            "event_type = 'evaluation.requested'",
            name="ck_outbox_events_supported_type",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_outbox_events_attempt_count"),
        Index(
            "ix_outbox_events_ready",
            "published_at",
            "next_attempt_at",
            "created_at",
        ),
    )

    event_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evaluation_jobs.evaluation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(128))


class BenchmarkRunRecord(Base):
    __tablename__ = "benchmark_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'partial', 'failed')",
            name="ck_benchmark_runs_status",
        ),
        CheckConstraint("samples_per_task > 0", name="ck_benchmark_runs_samples_positive"),
        CheckConstraint("planned_sample_count > 0", name="ck_benchmark_runs_planned_positive"),
        UniqueConstraint("idempotency_key", name="uq_benchmark_runs_idempotency_key"),
        Index("ix_benchmark_runs_created_at", "created_at"),
        Index("ix_benchmark_runs_status", "status"),
    )

    benchmark_run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_id: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_version: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    benchmark_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    coding_prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    coding_prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluator_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    benchmark_run_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    samples_per_task: Mapped[int] = mapped_column(Integer, nullable=False)
    planned_sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))


class BenchmarkModelConfigRecord(Base):
    __tablename__ = "benchmark_model_configs"
    __table_args__ = (
        UniqueConstraint("benchmark_run_id", "ordinal", name="uq_benchmark_model_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_benchmark_model_ordinal"),
        CheckConstraint("max_output_tokens > 0", name="ck_benchmark_model_max_tokens"),
        Index("ix_benchmark_model_configs_run", "benchmark_run_id"),
    )

    model_config_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    benchmark_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("benchmark_runs.benchmark_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_id: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)
    top_p: Mapped[float] = mapped_column(Float, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int | None] = mapped_column(Integer)
    coding_prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_configuration_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    pricing_version: Mapped[str | None] = mapped_column(Text)
    input_cost_per_million_tokens: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    output_cost_per_million_tokens: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    currency: Mapped[str | None] = mapped_column(String(3))


class BenchmarkSampleRecord(Base):
    __tablename__ = "benchmark_samples"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'generating', 'generated', 'evaluating', 'completed', "
            "'generation_failed', 'evaluation_failed', 'skipped')",
            name="ck_benchmark_samples_status",
        ),
        CheckConstraint("sample_index > 0", name="ck_benchmark_samples_index"),
        CheckConstraint("task_weight > 0", name="ck_benchmark_samples_weight"),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_benchmark_samples_attempts",
        ),
        UniqueConstraint(
            "model_config_id", "task_id", "sample_index", name="uq_benchmark_sample_plan"
        ),
        UniqueConstraint("evaluation_id", name="uq_benchmark_samples_evaluation"),
        Index("ix_benchmark_samples_run_status", "benchmark_run_id", "status"),
        Index("ix_benchmark_samples_lease", "lease_expires_at"),
        Index("ix_benchmark_samples_task", "task_id"),
    )

    benchmark_sample_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    benchmark_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("benchmark_runs.benchmark_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    model_config_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("benchmark_model_configs.model_config_id", ondelete="RESTRICT"),
        nullable=False,
    )
    evaluation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    task_version: Mapped[str] = mapped_column(Text, nullable=False)
    task_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    tests_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    task_weight: Mapped[float] = mapped_column(Float, nullable=False)
    sample_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(128))
    evaluation_duration_seconds: Mapped[float | None] = mapped_column(Float)
    total_duration_seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BenchmarkGenerationArtifactRecord(Base):
    __tablename__ = "benchmark_generation_artifacts"
    __table_args__ = (
        CheckConstraint("source_size > 0", name="ck_benchmark_artifacts_source_size"),
        CheckConstraint("generation_latency_ms >= 0", name="ck_benchmark_artifacts_latency"),
    )

    benchmark_sample_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("benchmark_samples.benchmark_sample_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_size: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_response_id: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    generation_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    pricing_version: Mapped[str | None] = mapped_column(Text)
    generation_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    currency: Mapped[str | None] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BenchmarkOutboxEventRecord(Base):
    __tablename__ = "benchmark_outbox_events"
    __table_args__ = (
        CheckConstraint(
            "event_type = 'benchmark.sample.requested'",
            name="ck_benchmark_outbox_event_type",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_benchmark_outbox_attempts"),
        Index(
            "ix_benchmark_outbox_ready",
            "published_at",
            "next_attempt_at",
            "created_at",
        ),
    )

    event_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    aggregate_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("benchmark_samples.benchmark_sample_id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(128))
