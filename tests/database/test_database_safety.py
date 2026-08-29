from pathlib import Path

import pytest

from app.db.migration_target import (
    ALEMBIC_DATABASE_URL_ATTRIBUTE,
    resolve_migration_database_url,
)
from tests.database.safety import (
    DESTRUCTIVE_DATABASE_OPT_IN_ENV,
    TEST_DATABASE_URL_ENV,
    DestructiveDatabaseOptInError,
    MissingTestDatabaseConfigurationError,
    UnsafeTestDatabaseError,
    resolve_safe_test_database,
    safe_alembic_config,
    safe_alembic_subprocess_environment,
)

TEST_URL = "postgresql+asyncpg://codejudge:codejudge@127.0.0.1:5432/codejudge_test"
DEVELOPMENT_URL = "postgresql+asyncpg://codejudge:codejudge@127.0.0.1:5432/codejudge"


def _enabled_environment(**overrides: str) -> dict[str, str]:
    return {
        TEST_DATABASE_URL_ENV: TEST_URL,
        DESTRUCTIVE_DATABASE_OPT_IN_ENV: "1",
        **overrides,
    }


def test_dedicated_test_database_is_accepted() -> None:
    target = resolve_safe_test_database(_enabled_environment())

    assert target.url == TEST_URL
    assert target.database_name == "codejudge_test"


def test_normal_development_database_is_rejected() -> None:
    with pytest.raises(UnsafeTestDatabaseError, match="must end in _test"):
        resolve_safe_test_database(_enabled_environment(**{TEST_DATABASE_URL_ENV: DEVELOPMENT_URL}))


def test_missing_test_database_never_falls_back_to_ambient_database_url() -> None:
    with pytest.raises(MissingTestDatabaseConfigurationError, match="never used as a fallback"):
        resolve_safe_test_database(
            {
                "DATABASE_URL": DEVELOPMENT_URL,
                DESTRUCTIVE_DATABASE_OPT_IN_ENV: "1",
            }
        )


def test_destructive_database_tests_require_explicit_opt_in() -> None:
    with pytest.raises(DestructiveDatabaseOptInError, match="authorize destructive"):
        resolve_safe_test_database({TEST_DATABASE_URL_ENV: TEST_URL})


def test_ambient_database_url_cannot_override_safe_alembic_target() -> None:
    environment = _enabled_environment(DATABASE_URL=DEVELOPMENT_URL)
    target = resolve_safe_test_database(environment)
    config = safe_alembic_config(
        target,
        str(Path(__file__).parents[2] / "alembic.ini"),
    )

    explicit_url = config.attributes[ALEMBIC_DATABASE_URL_ATTRIBUTE]
    assert resolve_migration_database_url(explicit_url, environment) == TEST_URL
    assert safe_alembic_subprocess_environment(target, environment)["DATABASE_URL"] == TEST_URL
