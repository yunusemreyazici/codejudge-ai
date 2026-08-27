import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from tests.database.conftest import DatabaseHarness
from tests.database.helpers import snapshot_fixture

pytestmark = pytest.mark.database


async def test_database_was_created_by_current_migration_head(
    database_harness: DatabaseHarness,
) -> None:
    async with database_harness.database.engine.connect() as connection:
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        table_name = await connection.scalar(text("SELECT to_regclass('public.evaluations')"))
        jobs_table = await connection.scalar(text("SELECT to_regclass('public.evaluation_jobs')"))
        outbox_table = await connection.scalar(text("SELECT to_regclass('public.outbox_events')"))

    assert revision == "20260827_0002"
    assert table_name == "evaluations"
    assert jobs_table == "evaluation_jobs"
    assert outbox_table == "outbox_events"


async def test_phase4_to_phase5_downgrade_upgrade_preserves_snapshot(
    database_harness: DatabaseHarness,
) -> None:
    snapshot = snapshot_fixture()
    await database_harness.repository.create(snapshot)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))

    await asyncio.to_thread(command.downgrade, config, "20260827_0001")
    async with database_harness.database.engine.connect() as connection:
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        jobs_table = await connection.scalar(text("SELECT to_regclass('public.evaluation_jobs')"))
        stored_snapshot = await connection.scalar(
            text("SELECT evaluation_id FROM evaluations WHERE evaluation_id = :evaluation_id"),
            {"evaluation_id": snapshot.evaluation_id},
        )

    assert revision == "20260827_0001"
    assert jobs_table is None
    assert stored_snapshot == snapshot.evaluation_id

    await asyncio.to_thread(command.upgrade, config, "head")
    assert await database_harness.repository.get(snapshot.evaluation_id) == snapshot
