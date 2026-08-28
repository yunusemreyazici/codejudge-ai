"""Add versioned supplemental AI assessment artifacts.

Revision ID: 20260827_0003
Revises: 20260827_0002
Create Date: 2026-08-27
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260827_0003"
down_revision: str | None = "20260827_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evaluations", sa.Column("ai_status", sa.String(length=32), nullable=True))
    op.add_column("evaluations", sa.Column("ai_reason", sa.String(length=128), nullable=True))
    op.add_column("evaluations", sa.Column("ai_score", sa.Float(), nullable=True))
    op.add_column("evaluations", sa.Column("judge_score", sa.Float(), nullable=True))
    op.add_column("evaluations", sa.Column("adversarial_robustness", sa.Float(), nullable=True))
    op.add_column(
        "evaluations",
        sa.Column("ai_reproducibility_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "evaluations",
        sa.Column(
            "ai_judge_results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "evaluations",
        sa.Column(
            "ai_adversarial_results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "evaluations",
        sa.Column("ai_provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        "ck_evaluations_ai_status",
        "evaluations",
        "ai_status IS NULL OR ai_status IN "
        "('disabled', 'completed', 'partial', 'unavailable', 'disputed', 'skipped')",
    )
    op.create_check_constraint(
        "ck_evaluations_ai_score",
        "evaluations",
        "ai_score IS NULL OR ai_score BETWEEN 0 AND 100",
    )
    op.create_check_constraint(
        "ck_evaluations_judge_score",
        "evaluations",
        "judge_score IS NULL OR judge_score BETWEEN 0 AND 100",
    )
    op.create_check_constraint(
        "ck_evaluations_adversarial_robustness",
        "evaluations",
        "adversarial_robustness IS NULL OR adversarial_robustness BETWEEN 0 AND 100",
    )
    op.create_check_constraint(
        "ck_evaluations_ai_fingerprint",
        "evaluations",
        "ai_reproducibility_fingerprint IS NULL OR length(ai_reproducibility_fingerprint) = 64",
    )
    op.create_index("ix_evaluations_ai_status", "evaluations", ["ai_status"])

    op.add_column(
        "evaluation_jobs",
        sa.Column(
            "expected_ai_identity",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    legacy_identity = {
        "enabled": False,
        "policy_version": "1",
        "judge_prompt_version": "1",
        "judge_prompt_hash": "legacy",
        "adversarial_prompt_version": "1",
        "adversarial_prompt_hash": "legacy",
        "provider_id": None,
        "judge_models": [],
        "adversarial_model": None,
        "temperature": 0.0,
        "top_p": 1.0,
        "max_output_tokens": 2000,
        "reference_fingerprint": None,
    }
    op.execute(
        sa.text(
            "UPDATE evaluation_jobs "
            "SET expected_ai_identity = CAST(:identity AS jsonb) "
            "WHERE expected_ai_identity IS NULL"
        ).bindparams(identity=json.dumps(legacy_identity, separators=(",", ":")))
    )
    op.alter_column("evaluation_jobs", "expected_ai_identity", nullable=False)


def downgrade() -> None:
    op.drop_column("evaluation_jobs", "expected_ai_identity")
    op.drop_index("ix_evaluations_ai_status", table_name="evaluations")
    op.drop_constraint("ck_evaluations_ai_fingerprint", "evaluations", type_="check")
    op.drop_constraint("ck_evaluations_adversarial_robustness", "evaluations", type_="check")
    op.drop_constraint("ck_evaluations_judge_score", "evaluations", type_="check")
    op.drop_constraint("ck_evaluations_ai_score", "evaluations", type_="check")
    op.drop_constraint("ck_evaluations_ai_status", "evaluations", type_="check")
    op.drop_column("evaluations", "ai_provenance")
    op.drop_column("evaluations", "ai_adversarial_results")
    op.drop_column("evaluations", "ai_judge_results")
    op.drop_column("evaluations", "ai_reproducibility_fingerprint")
    op.drop_column("evaluations", "adversarial_robustness")
    op.drop_column("evaluations", "judge_score")
    op.drop_column("evaluations", "ai_score")
    op.drop_column("evaluations", "ai_reason")
    op.drop_column("evaluations", "ai_status")
