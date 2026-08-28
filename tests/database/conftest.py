from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.benchmarks.repositories import SqlAlchemyBenchmarkRepository
from app.db.repositories import SqlAlchemyEvaluationRepository
from app.db.session import Database
from app.jobs.repositories import SqlAlchemyEvaluationJobRepository


@dataclass(frozen=True, slots=True)
class DatabaseHarness:
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
    database_url = os.getenv("CODEJUDGE_TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("CODEJUDGE_TEST_DATABASE_URL is not configured")
    parsed = make_url(database_url)
    if parsed.drivername != "postgresql+asyncpg" or not (parsed.database or "").endswith("_test"):
        raise RuntimeError("Database tests require a dedicated postgresql+asyncpg *_test database")

    database = Database(database_url)
    async with database.engine.begin() as connection:
        await connection.execute(text(TRUNCATE_ALL))
    try:
        yield DatabaseHarness(
            database=database,
            repository=SqlAlchemyEvaluationRepository(database.session_factory),
            job_repository=SqlAlchemyEvaluationJobRepository(database.session_factory),
            benchmark_repository=SqlAlchemyBenchmarkRepository(database.session_factory),
        )
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(text(TRUNCATE_ALL))
        await database.dispose()
