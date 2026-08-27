"""Create immutable evaluation snapshots.

Revision ID: 20260827_0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260827_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluations",
        sa.Column("evaluation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("task_version", sa.Text(), nullable=False),
        sa.Column("task_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("tests_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("execution_backend", sa.String(length=32), nullable=False),
        sa.Column("sandbox_image", sa.Text(), nullable=True),
        sa.Column("sandbox_image_id", sa.Text(), nullable=True),
        sa.Column("codejudge_version", sa.Text(), nullable=False),
        sa.Column("scoring_policy_version", sa.Text(), nullable=False),
        sa.Column("analyzer_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tests_passed", sa.Integer(), nullable=False),
        sa.Column("tests_failed", sa.Integer(), nullable=False),
        sa.Column("tests_total", sa.Integer(), nullable=False),
        sa.Column("test_duration_seconds", sa.Float(), nullable=False),
        sa.Column("timed_out", sa.Boolean(), nullable=False),
        sa.Column("oom_killed", sa.Boolean(), nullable=False),
        sa.Column("correctness_score", sa.Float(), nullable=False),
        sa.Column("code_quality_score", sa.Float(), nullable=True),
        sa.Column("type_safety_score", sa.Float(), nullable=True),
        sa.Column("security_score", sa.Float(), nullable=True),
        sa.Column("complexity_score", sa.Float(), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("complexity", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("execution_findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("analysis_findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reproducibility_fingerprint", sa.String(length=64), nullable=False),
        sa.CheckConstraint("source_size > 0", name="ck_evaluations_source_size_positive"),
        sa.CheckConstraint("length(source_hash) = 64", name="ck_evaluations_source_hash_length"),
        sa.CheckConstraint(
            "length(task_fingerprint) = 64", name="ck_evaluations_task_fingerprint_length"
        ),
        sa.CheckConstraint(
            "length(tests_fingerprint) = 64", name="ck_evaluations_tests_fingerprint_length"
        ),
        sa.CheckConstraint(
            "length(reproducibility_fingerprint) = 64",
            name="ck_evaluations_reproducibility_fingerprint_length",
        ),
        sa.CheckConstraint(
            "tests_passed >= 0 AND tests_failed >= 0 AND tests_total >= 0",
            name="ck_evaluations_test_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "tests_passed + tests_failed = tests_total",
            name="ck_evaluations_test_counts_total",
        ),
        sa.CheckConstraint("duration_seconds >= 0", name="ck_evaluations_duration_nonnegative"),
        sa.CheckConstraint(
            "correctness_score BETWEEN 0 AND 100", name="ck_evaluations_correctness_score"
        ),
        sa.CheckConstraint("final_score BETWEEN 0 AND 100", name="ck_evaluations_final_score"),
        sa.CheckConstraint(
            "code_quality_score IS NULL OR code_quality_score BETWEEN 0 AND 100",
            name="ck_evaluations_code_quality_score",
        ),
        sa.CheckConstraint(
            "type_safety_score IS NULL OR type_safety_score BETWEEN 0 AND 100",
            name="ck_evaluations_type_safety_score",
        ),
        sa.CheckConstraint(
            "security_score IS NULL OR security_score BETWEEN 0 AND 100",
            name="ck_evaluations_security_score",
        ),
        sa.CheckConstraint(
            "complexity_score IS NULL OR complexity_score BETWEEN 0 AND 100",
            name="ck_evaluations_complexity_score",
        ),
        sa.PrimaryKeyConstraint("evaluation_id"),
    )
    op.create_index("ix_evaluations_created_at", "evaluations", ["created_at"])
    op.create_index("ix_evaluations_final_score", "evaluations", ["final_score"])
    op.create_index("ix_evaluations_source_hash", "evaluations", ["source_hash"])
    op.create_index("ix_evaluations_task_id", "evaluations", ["task_id"])
    op.execute(
        """
        CREATE FUNCTION codejudge_prevent_evaluation_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'evaluation snapshots are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER evaluations_immutable
        BEFORE UPDATE OR DELETE ON evaluations
        FOR EACH ROW EXECUTE FUNCTION codejudge_prevent_evaluation_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS evaluations_immutable ON evaluations")
    op.execute("DROP FUNCTION IF EXISTS codejudge_prevent_evaluation_mutation()")
    op.drop_index("ix_evaluations_task_id", table_name="evaluations")
    op.drop_index("ix_evaluations_source_hash", table_name="evaluations")
    op.drop_index("ix_evaluations_final_score", table_name="evaluations")
    op.drop_index("ix_evaluations_created_at", table_name="evaluations")
    op.drop_table("evaluations")
