"""Add durable Phase 7 benchmark runs, samples, artifacts, and outbox.

Revision ID: 20260828_0004
Revises: 20260827_0003
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0004"
down_revision: str | None = "20260827_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "benchmark_runs",
        sa.Column("benchmark_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.Text(), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("dataset_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("benchmark_policy_version", sa.Text(), nullable=False),
        sa.Column("coding_prompt_version", sa.Text(), nullable=False),
        sa.Column("coding_prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("evaluator_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("benchmark_run_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("samples_per_task", sa.Integer(), nullable=False),
        sa.Column("planned_sample_count", sa.Integer(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'partial', 'failed')",
            name="ck_benchmark_runs_status",
        ),
        sa.CheckConstraint("samples_per_task > 0", name="ck_benchmark_runs_samples_positive"),
        sa.CheckConstraint("planned_sample_count > 0", name="ck_benchmark_runs_planned_positive"),
        sa.PrimaryKeyConstraint("benchmark_run_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_benchmark_runs_idempotency_key"),
    )
    op.create_index("ix_benchmark_runs_created_at", "benchmark_runs", ["created_at"])
    op.create_index("ix_benchmark_runs_status", "benchmark_runs", ["status"])

    op.create_table(
        "benchmark_model_configs",
        sa.Column("model_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("benchmark_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("top_p", sa.Float(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("coding_prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("model_configuration_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("pricing_version", sa.Text(), nullable=True),
        sa.Column("input_cost_per_million_tokens", sa.Numeric(24, 12), nullable=True),
        sa.Column("output_cost_per_million_tokens", sa.Numeric(24, 12), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.CheckConstraint("ordinal >= 0", name="ck_benchmark_model_ordinal"),
        sa.CheckConstraint("max_output_tokens > 0", name="ck_benchmark_model_max_tokens"),
        sa.ForeignKeyConstraint(
            ["benchmark_run_id"], ["benchmark_runs.benchmark_run_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("model_config_id"),
        sa.UniqueConstraint("benchmark_run_id", "ordinal", name="uq_benchmark_model_ordinal"),
    )
    op.create_index(
        "ix_benchmark_model_configs_run", "benchmark_model_configs", ["benchmark_run_id"]
    )

    op.create_table(
        "benchmark_samples",
        sa.Column("benchmark_sample_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("benchmark_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evaluation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("task_version", sa.Text(), nullable=False),
        sa.Column("task_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("tests_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("task_weight", sa.Float(), nullable=False),
        sa.Column("sample_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("evaluation_duration_seconds", sa.Float(), nullable=True),
        sa.Column("total_duration_seconds", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'generating', 'generated', 'evaluating', 'completed', "
            "'generation_failed', 'evaluation_failed', 'skipped')",
            name="ck_benchmark_samples_status",
        ),
        sa.CheckConstraint("sample_index > 0", name="ck_benchmark_samples_index"),
        sa.CheckConstraint("task_weight > 0", name="ck_benchmark_samples_weight"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_benchmark_samples_attempts",
        ),
        sa.ForeignKeyConstraint(
            ["benchmark_run_id"], ["benchmark_runs.benchmark_run_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["model_config_id"],
            ["benchmark_model_configs.model_config_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("benchmark_sample_id"),
        sa.UniqueConstraint("evaluation_id", name="uq_benchmark_samples_evaluation"),
        sa.UniqueConstraint(
            "model_config_id", "task_id", "sample_index", name="uq_benchmark_sample_plan"
        ),
    )
    op.create_index(
        "ix_benchmark_samples_run_status", "benchmark_samples", ["benchmark_run_id", "status"]
    )
    op.create_index("ix_benchmark_samples_lease", "benchmark_samples", ["lease_expires_at"])
    op.create_index("ix_benchmark_samples_task", "benchmark_samples", ["task_id"])

    op.create_table(
        "benchmark_generation_artifacts",
        sa.Column("benchmark_sample_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source_size", sa.Integer(), nullable=False),
        sa.Column("generation_attempts", sa.Integer(), nullable=False),
        sa.Column("provider_response_id", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("generation_latency_ms", sa.Integer(), nullable=False),
        sa.Column("pricing_version", sa.Text(), nullable=True),
        sa.Column("generation_cost", sa.Numeric(24, 12), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_size > 0", name="ck_benchmark_artifacts_source_size"),
        sa.CheckConstraint(
            "generation_attempts > 0", name="ck_benchmark_artifacts_attempts_positive"
        ),
        sa.CheckConstraint("generation_latency_ms >= 0", name="ck_benchmark_artifacts_latency"),
        sa.ForeignKeyConstraint(
            ["benchmark_sample_id"],
            ["benchmark_samples.benchmark_sample_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("benchmark_sample_id"),
    )

    op.create_table(
        "benchmark_outbox_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "event_type = 'benchmark.sample.requested'",
            name="ck_benchmark_outbox_event_type",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_benchmark_outbox_attempts"),
        sa.ForeignKeyConstraint(
            ["aggregate_id"], ["benchmark_samples.benchmark_sample_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_benchmark_outbox_ready",
        "benchmark_outbox_events",
        ["published_at", "next_attempt_at", "created_at"],
    )
    op.execute(
        """
        CREATE FUNCTION codejudge_protect_benchmark_run_identity()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'benchmark run identity is immutable';
            END IF;
            IF OLD.dataset_id IS DISTINCT FROM NEW.dataset_id
               OR OLD.dataset_version IS DISTINCT FROM NEW.dataset_version
               OR OLD.dataset_fingerprint IS DISTINCT FROM NEW.dataset_fingerprint
               OR OLD.benchmark_policy_version IS DISTINCT FROM NEW.benchmark_policy_version
               OR OLD.coding_prompt_version IS DISTINCT FROM NEW.coding_prompt_version
               OR OLD.coding_prompt_hash IS DISTINCT FROM NEW.coding_prompt_hash
               OR OLD.evaluator_fingerprint IS DISTINCT FROM NEW.evaluator_fingerprint
               OR OLD.benchmark_run_fingerprint IS DISTINCT FROM NEW.benchmark_run_fingerprint
               OR OLD.samples_per_task IS DISTINCT FROM NEW.samples_per_task
               OR OLD.planned_sample_count IS DISTINCT FROM NEW.planned_sample_count
               OR OLD.request_fingerprint IS DISTINCT FROM NEW.request_fingerprint
               OR OLD.idempotency_key IS DISTINCT FROM NEW.idempotency_key THEN
                RAISE EXCEPTION 'benchmark run identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER benchmark_runs_identity_immutable
        BEFORE UPDATE OR DELETE ON benchmark_runs
        FOR EACH ROW EXECUTE FUNCTION codejudge_protect_benchmark_run_identity()
        """
    )
    op.execute(
        """
        CREATE FUNCTION codejudge_prevent_benchmark_artifact_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'benchmark configuration and artifacts are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER benchmark_model_configs_immutable
        BEFORE UPDATE OR DELETE ON benchmark_model_configs
        FOR EACH ROW EXECUTE FUNCTION codejudge_prevent_benchmark_artifact_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER benchmark_generation_artifacts_immutable
        BEFORE UPDATE OR DELETE ON benchmark_generation_artifacts
        FOR EACH ROW EXECUTE FUNCTION codejudge_prevent_benchmark_artifact_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION codejudge_protect_benchmark_sample_identity()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'benchmark sample identity is immutable';
            END IF;
            IF OLD.benchmark_run_id IS DISTINCT FROM NEW.benchmark_run_id
               OR OLD.model_config_id IS DISTINCT FROM NEW.model_config_id
               OR OLD.evaluation_id IS DISTINCT FROM NEW.evaluation_id
               OR OLD.task_id IS DISTINCT FROM NEW.task_id
               OR OLD.task_version IS DISTINCT FROM NEW.task_version
               OR OLD.task_fingerprint IS DISTINCT FROM NEW.task_fingerprint
               OR OLD.tests_fingerprint IS DISTINCT FROM NEW.tests_fingerprint
               OR OLD.task_weight IS DISTINCT FROM NEW.task_weight
               OR OLD.sample_index IS DISTINCT FROM NEW.sample_index
               OR OLD.max_attempts IS DISTINCT FROM NEW.max_attempts THEN
                RAISE EXCEPTION 'benchmark sample identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER benchmark_samples_identity_immutable
        BEFORE UPDATE OR DELETE ON benchmark_samples
        FOR EACH ROW EXECUTE FUNCTION codejudge_protect_benchmark_sample_identity()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS benchmark_samples_identity_immutable ON benchmark_samples")
    op.execute("DROP FUNCTION IF EXISTS codejudge_protect_benchmark_sample_identity()")
    op.execute(
        "DROP TRIGGER IF EXISTS benchmark_generation_artifacts_immutable "
        "ON benchmark_generation_artifacts"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS benchmark_model_configs_immutable ON benchmark_model_configs"
    )
    op.execute("DROP FUNCTION IF EXISTS codejudge_prevent_benchmark_artifact_mutation()")
    op.execute("DROP TRIGGER IF EXISTS benchmark_runs_identity_immutable ON benchmark_runs")
    op.execute("DROP FUNCTION IF EXISTS codejudge_protect_benchmark_run_identity()")
    op.drop_index("ix_benchmark_outbox_ready", table_name="benchmark_outbox_events")
    op.drop_table("benchmark_outbox_events")
    op.drop_table("benchmark_generation_artifacts")
    op.drop_index("ix_benchmark_samples_task", table_name="benchmark_samples")
    op.drop_index("ix_benchmark_samples_lease", table_name="benchmark_samples")
    op.drop_index("ix_benchmark_samples_run_status", table_name="benchmark_samples")
    op.drop_table("benchmark_samples")
    op.drop_index("ix_benchmark_model_configs_run", table_name="benchmark_model_configs")
    op.drop_table("benchmark_model_configs")
    op.drop_index("ix_benchmark_runs_status", table_name="benchmark_runs")
    op.drop_index("ix_benchmark_runs_created_at", table_name="benchmark_runs")
    op.drop_table("benchmark_runs")
