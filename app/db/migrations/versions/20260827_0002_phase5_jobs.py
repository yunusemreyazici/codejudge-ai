"""Add durable evaluation jobs and transactional outbox.

Revision ID: 20260827_0002
Revises: 20260827_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260827_0002"
down_revision: str | None = "20260827_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_jobs",
        sa.Column("evaluation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("task_version", sa.Text(), nullable=False),
        sa.Column("task_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("tests_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source_size", sa.Integer(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_category", sa.String(length=64), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("snapshot_created", sa.Boolean(), nullable=False),
        sa.Column(
            "expected_execution",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "expected_analyzer_versions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("expected_scoring_policy_version", sa.Text(), nullable=False),
        sa.Column("expected_codejudge_version", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'completed', 'failed')",
            name="ck_evaluation_jobs_status",
        ),
        sa.CheckConstraint("source_size > 0", name="ck_evaluation_jobs_source_size_positive"),
        sa.CheckConstraint(
            "length(source_hash) = 64", name="ck_evaluation_jobs_source_hash_length"
        ),
        sa.CheckConstraint(
            "length(task_fingerprint) = 64",
            name="ck_evaluation_jobs_task_fingerprint_length",
        ),
        sa.CheckConstraint(
            "length(tests_fingerprint) = 64",
            name="ck_evaluation_jobs_tests_fingerprint_length",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_evaluation_jobs_request_fingerprint_length",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_evaluation_jobs_attempts",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND snapshot_created) OR "
            "(status <> 'completed' AND NOT snapshot_created)",
            name="ck_evaluation_jobs_snapshot_state",
        ),
        sa.PrimaryKeyConstraint("evaluation_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_evaluation_jobs_created_at", "evaluation_jobs", ["created_at"])
    op.create_index("ix_evaluation_jobs_lease_expires_at", "evaluation_jobs", ["lease_expires_at"])
    op.create_index(
        "ix_evaluation_jobs_status_next_attempt",
        "evaluation_jobs",
        ["status", "next_attempt_at"],
    )
    op.create_index("ix_evaluation_jobs_task_id", "evaluation_jobs", ["task_id"])

    op.create_table(
        "outbox_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "event_type = 'evaluation.requested'",
            name="ck_outbox_events_supported_type",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_events_attempt_count"),
        sa.ForeignKeyConstraint(
            ["aggregate_id"],
            ["evaluation_jobs.evaluation_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_outbox_events_ready",
        "outbox_events",
        ["published_at", "next_attempt_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_ready", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_evaluation_jobs_task_id", table_name="evaluation_jobs")
    op.drop_index("ix_evaluation_jobs_status_next_attempt", table_name="evaluation_jobs")
    op.drop_index("ix_evaluation_jobs_lease_expires_at", table_name="evaluation_jobs")
    op.drop_index("ix_evaluation_jobs_created_at", table_name="evaluation_jobs")
    op.drop_table("evaluation_jobs")
