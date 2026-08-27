"""Typed SQLAlchemy mappings for append-only evaluation snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
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
