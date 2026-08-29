"""Persist provider-level benchmark generation concurrency.

Revision ID: 20260829_0006
Revises: 20260829_0005
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0006"
down_revision: str | None = "20260829_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benchmark_model_configs",
        sa.Column("max_concurrent_requests", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_benchmark_model_max_concurrent_requests",
        "benchmark_model_configs",
        "max_concurrent_requests IS NULL OR "
        "(max_concurrent_requests > 0 AND max_concurrent_requests <= 100)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_benchmark_model_max_concurrent_requests",
        "benchmark_model_configs",
        type_="check",
    )
    op.drop_column("benchmark_model_configs", "max_concurrent_requests")
