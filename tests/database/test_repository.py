from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

from app.db.models import EvaluationRecord
from app.snapshots.fingerprints import source_identity
from tests.database.conftest import DatabaseHarness
from tests.database.helpers import snapshot_fixture

pytestmark = pytest.mark.database


async def test_snapshot_round_trip_preserves_complete_reasoning(
    database_harness: DatabaseHarness,
) -> None:
    source = "class LRUCache:\n    pass\n"
    snapshot = snapshot_fixture(source=source)

    created = await database_harness.repository.create(snapshot)
    stored = await database_harness.repository.get(snapshot.evaluation_id)

    assert created == snapshot
    assert stored == snapshot
    assert stored is not None
    assert stored.source_text == source
    assert (stored.source_hash, stored.source_size) == source_identity(source)
    assert stored.task_version == "1.0"
    assert stored.analyzer_versions == snapshot.analyzer_versions
    assert stored.scoring_policy_version == "1"
    assert stored.execution == snapshot.execution
    assert stored.execution_findings == snapshot.execution_findings
    assert stored.analysis_findings == snapshot.analysis_findings
    assert stored.complexity == snapshot.complexity


async def test_identical_snapshots_receive_distinct_ids(
    database_harness: DatabaseHarness,
) -> None:
    first = snapshot_fixture(evaluation_id=uuid4())
    second = snapshot_fixture(evaluation_id=uuid4())

    await database_harness.repository.create(first)
    await database_harness.repository.create(second)

    assert first.evaluation_id != second.evaluation_id
    assert first.source_hash == second.source_hash
    assert first.reproducibility_fingerprint == second.reproducibility_fingerprint
    assert await database_harness.repository.get(first.evaluation_id) == first
    assert await database_harness.repository.get(second.evaluation_id) == second


async def test_list_is_newest_first_and_paginates(
    database_harness: DatabaseHarness,
) -> None:
    started = datetime(2026, 8, 27, 10, tzinfo=UTC)
    snapshots = [
        snapshot_fixture(created_at=started + timedelta(seconds=index)) for index in range(3)
    ]
    for snapshot in snapshots:
        await database_harness.repository.create(snapshot)

    first_page = await database_harness.repository.list(limit=2, offset=0)
    second_page = await database_harness.repository.list(limit=2, offset=2)

    assert [item.evaluation_id for item in first_page] == [
        snapshots[2].evaluation_id,
        snapshots[1].evaluation_id,
    ]
    assert [item.evaluation_id for item in second_page] == [snapshots[0].evaluation_id]


async def test_list_filters_task_language_and_score(
    database_harness: DatabaseHarness,
) -> None:
    snapshot = snapshot_fixture()
    await database_harness.repository.create(snapshot)

    matching = await database_harness.repository.list(
        limit=10,
        offset=0,
        task_id="lru-cache",
        language="python",
        minimum_score=80,
        maximum_score=90,
    )
    wrong_task = await database_harness.repository.list(limit=10, offset=0, task_id="different")
    wrong_language = await database_harness.repository.list(
        limit=10, offset=0, language="javascript"
    )
    wrong_score = await database_harness.repository.list(limit=10, offset=0, minimum_score=99)

    assert [item.evaluation_id for item in matching] == [snapshot.evaluation_id]
    assert wrong_task == []
    assert wrong_language == []
    assert wrong_score == []


async def test_unknown_evaluation_returns_none(database_harness: DatabaseHarness) -> None:
    assert await database_harness.repository.get(uuid4()) is None
    assert await database_harness.repository.check_capability() is True


async def test_database_rejects_snapshot_mutation(
    database_harness: DatabaseHarness,
) -> None:
    snapshot = snapshot_fixture()
    await database_harness.repository.create(snapshot)

    with pytest.raises(DBAPIError, match="evaluation snapshots are immutable"):
        async with database_harness.database.engine.begin() as connection:
            await connection.execute(
                update(EvaluationRecord)
                .where(EvaluationRecord.evaluation_id == snapshot.evaluation_id)
                .values(final_score=0)
            )

    assert await database_harness.repository.get(snapshot.evaluation_id) == snapshot
