"""Hard guards for tests that truncate tables or downgrade database schemas."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from alembic.config import Config
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from app.db.migration_target import ALEMBIC_DATABASE_URL_ATTRIBUTE

TEST_DATABASE_URL_ENV = "CODEJUDGE_TEST_DATABASE_URL"
DESTRUCTIVE_DATABASE_OPT_IN_ENV = "CODEJUDGE_ALLOW_DESTRUCTIVE_DATABASE_TESTS"
REQUIRE_DATABASE_TESTS_ENV = "CODEJUDGE_REQUIRE_DATABASE"


class DatabaseTestSafetyError(RuntimeError):
    """Base error for an absent opt-in or unsafe destructive-test target."""


class MissingTestDatabaseConfigurationError(DatabaseTestSafetyError):
    """The dedicated test URL was not configured."""


class DestructiveDatabaseOptInError(DatabaseTestSafetyError):
    """Destructive database tests were not explicitly enabled."""


class UnsafeTestDatabaseError(DatabaseTestSafetyError):
    """The configured target is not recognizable as a dedicated test database."""


@dataclass(frozen=True, slots=True)
class SafeTestDatabaseTarget:
    url: str
    parsed: URL

    @property
    def database_name(self) -> str:
        return self.parsed.database or ""


def resolve_safe_test_database(
    environment: Mapping[str, str] | None = None,
) -> SafeTestDatabaseTarget:
    """Resolve only the dedicated test URL and reject unsafe destructive targets."""
    values = os.environ if environment is None else environment
    database_url = values.get(TEST_DATABASE_URL_ENV, "").strip()
    if not database_url:
        raise MissingTestDatabaseConfigurationError(
            f"{TEST_DATABASE_URL_ENV} is required; DATABASE_URL is never used as a fallback"
        )
    if values.get(DESTRUCTIVE_DATABASE_OPT_IN_ENV, "").strip() != "1":
        raise DestructiveDatabaseOptInError(
            f"Set {DESTRUCTIVE_DATABASE_OPT_IN_ENV}=1 to authorize destructive database tests"
        )
    try:
        parsed = make_url(database_url)
    except ArgumentError as error:
        raise UnsafeTestDatabaseError(
            f"{TEST_DATABASE_URL_ENV} must be a valid SQLAlchemy URL"
        ) from error
    if parsed.drivername != "postgresql+asyncpg":
        raise UnsafeTestDatabaseError(
            f"{TEST_DATABASE_URL_ENV} must use the postgresql+asyncpg driver"
        )
    database_name = parsed.database or ""
    if not database_name.endswith("_test"):
        raise UnsafeTestDatabaseError(
            f"Refusing destructive tests: {TEST_DATABASE_URL_ENV} database name must end in _test"
        )
    return SafeTestDatabaseTarget(url=database_url, parsed=parsed)


def safe_alembic_config(target: SafeTestDatabaseTarget, config_path: str) -> Config:
    """Pin in-process Alembic execution to the already validated test target."""
    config = Config(config_path)
    config.attributes[ALEMBIC_DATABASE_URL_ATTRIBUTE] = target.url
    return config


def safe_alembic_subprocess_environment(
    target: SafeTestDatabaseTarget,
    inherited: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build an explicit environment for any future Alembic subprocess invocation."""
    environment = dict(os.environ if inherited is None else inherited)
    environment[TEST_DATABASE_URL_ENV] = target.url
    environment["DATABASE_URL"] = target.url
    return environment
