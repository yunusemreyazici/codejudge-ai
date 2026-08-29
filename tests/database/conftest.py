from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.benchmarks.repositories import SqlAlchemyBenchmarkRepository
from app.db.repositories import SqlAlchemyEvaluationRepository
from app.db.session import Database
from app.jobs.repositories import SqlAlchemyEvaluationJobRepository
from tests.database.safety import (
    REQUIRE_DATABASE_TESTS_ENV,
    DestructiveDatabaseOptInError,
    MissingTestDatabaseConfigurationError,
    SafeTestDatabaseTarget,
    resolve_safe_test_database,
)


@dataclass(frozen=True, slots=True)
class DatabaseHarness:
    target: SafeTestDatabaseTarget
    database: Database
    repository: SqlAlchemyEvaluationRepository
    job_repository: SqlAlchemyEvaluationJobRepository
    benchmark_repository: SqlAlchemyBenchmarkRepository


TRUNCATE_ALL = """TRUNCATE TABLE
benchmark_outbox_events,
benchmark_generation_artifacts,
benchmark_samples,
benchmark_model_configs,
benchmark_runs,
outbox_events,
evaluation_jobs,
evaluations
"""


@pytest_asyncio.fixture
async def database_harness() -> AsyncIterator[DatabaseHarness]:
    try:
        target = resolve_safe_test_database()
    except (MissingTestDatabaseConfigurationError, DestructiveDatabaseOptInError) as error:
        if os.getenv(REQUIRE_DATABASE_TESTS_ENV, "").strip() == "1":
            pytest.fail(str(error), pytrace=False)
        pytest.skip(str(error))

    database = Database(target.url)
    async with database.engine.begin() as connection:
        await connection.execute(text(TRUNCATE_ALL))
    try:
        yield DatabaseHarness(
            target=target,
            database=database,
            repository=SqlAlchemyEvaluationRepository(database.session_factory),
            job_repository=SqlAlchemyEvaluationJobRepository(database.session_factory),
            benchmark_repository=SqlAlchemyBenchmarkRepository(database.session_factory),
        )
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(text(TRUNCATE_ALL))
        await database.dispose()
