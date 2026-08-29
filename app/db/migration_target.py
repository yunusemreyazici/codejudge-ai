"""Resolve an Alembic target with an explicit caller override when supplied."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ALEMBIC_DATABASE_URL_ATTRIBUTE = "codejudge_database_url"


def resolve_migration_database_url(
    explicit_url: object,
    environment: Mapping[str, str],
) -> str:
    """Prefer an explicit Alembic Config target over ambient process state."""
    database_url = explicit_url.strip() if isinstance(explicit_url, str) else ""
    if not database_url:
        database_url = environment.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for Alembic migrations")
    try:
        parsed = make_url(database_url)
    except ArgumentError as error:
        raise RuntimeError("Alembic DATABASE_URL must be a valid SQLAlchemy URL") from error
    if parsed.drivername != "postgresql+asyncpg":
        raise RuntimeError("Alembic requires a postgresql+asyncpg DATABASE_URL")
    return database_url
