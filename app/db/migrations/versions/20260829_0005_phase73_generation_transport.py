"""Persist benchmark generation transport provenance.

Revision ID: 20260829_0005
Revises: 20260828_0004
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0005"
down_revision: str | None = "20260828_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benchmark_model_configs",
        sa.Column(
            "output_mode",
            sa.String(length=32),
            nullable=False,
            server_default="structured_json",
        ),
    )
    op.add_column(
        "benchmark_model_configs",
        sa.Column(
            "request_timeout_seconds",
            sa.Float(),
            nullable=False,
            server_default="30",
        ),
    )
    op.create_check_constraint(
        "ck_benchmark_model_output_mode",
        "benchmark_model_configs",
        "output_mode IN ('structured_json', 'raw_source')",
    )
    op.create_check_constraint(
        "ck_benchmark_model_request_timeout",
        "benchmark_model_configs",
        "request_timeout_seconds > 0 AND request_timeout_seconds <= 600",
    )
    op.alter_column("benchmark_model_configs", "output_mode", server_default=None)
    op.alter_column("benchmark_model_configs", "request_timeout_seconds", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "ck_benchmark_model_request_timeout",
        "benchmark_model_configs",
        type_="check",
    )
    op.drop_constraint(
        "ck_benchmark_model_output_mode",
        "benchmark_model_configs",
        type_="check",
    )
    op.drop_column("benchmark_model_configs", "request_timeout_seconds")
    op.drop_column("benchmark_model_configs", "output_mode")
