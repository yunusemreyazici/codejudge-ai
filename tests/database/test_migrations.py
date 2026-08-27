import pytest
from sqlalchemy import text

from tests.database.conftest import DatabaseHarness

pytestmark = pytest.mark.database


async def test_database_was_created_by_current_migration_head(
    database_harness: DatabaseHarness,
) -> None:
    async with database_harness.database.engine.connect() as connection:
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        table_name = await connection.scalar(text("SELECT to_regclass('public.evaluations')"))

    assert revision == "20260827_0001"
    assert table_name == "evaluations"
