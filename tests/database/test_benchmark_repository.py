import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.benchmarks.models import (
    BenchmarkModelConfig,
    BenchmarkRun,
    BenchmarkRunStatus,
    BenchmarkSample,
    BenchmarkSampleStatus,
    GeneratedSolutionArtifact,
)
from app.jobs.repositories import IdempotencyConflictError
from app.snapshots.fingerprints import source_identity
from tests.database.conftest import DatabaseHarness
from tests.database.helpers import snapshot_fixture

pytestmark = pytest.mark.database
NOW = datetime(2026, 8, 28, 10, tzinfo=UTC)
SOURCE = "class LRUCache: pass\n"


def _plan(*, idempotency_key: str | None = "benchmark-key") -> tuple:
    run_id = uuid4()
    config = BenchmarkModelConfig(
        model_config_id=uuid4(),
        benchmark_run_id=run_id,
        ordinal=0,
        provider_id="fake",
        model="good",
        display_name="Good",
        temperature=0,
        top_p=1,
        max_output_tokens=100,
        max_concurrent_requests=1,
        coding_prompt_hash="a" * 64,
        model_configuration_fingerprint="b" * 64,
    )
    run = BenchmarkRun(
        benchmark_run_id=run_id,
        created_at=NOW,
        status=BenchmarkRunStatus.QUEUED,
        dataset_id="codejudge-core",
        dataset_version="1",
        dataset_fingerprint="c" * 64,
        benchmark_policy_version="1",
        coding_prompt_version="1",
        coding_prompt_hash="a" * 64,
        evaluator_fingerprint="d" * 64,
        benchmark_run_fingerprint="e" * 64,
        samples_per_task=1,
        planned_sample_count=1,
        request_fingerprint="f" * 64,
        idempotency_key=idempotency_key,
        model_configs=(config,),
    )
    sample = BenchmarkSample(
        benchmark_sample_id=uuid4(),
        benchmark_run_id=run_id,
        model_config_id=config.model_config_id,
        evaluation_id=uuid4(),
        task_id="lru-cache",
        task_version="1.0",
        task_fingerprint="1" * 64,
        tests_fingerprint="2" * 64,
        task_weight=1,
        sample_index=1,
        status=BenchmarkSampleStatus.QUEUED,
        attempt_count=0,
        max_attempts=3,
        created_at=NOW,
        updated_at=NOW,
    )
    return run, config, sample


async def test_benchmark_repository_persists_plan_artifact_and_atomic_completion(
    database_harness: DatabaseHarness,
) -> None:
    repository = database_harness.benchmark_repository
    run, config, sample = _plan()
    stored, created = await repository.create_plan(run, [config], [sample])

    assert created is True
    assert stored.benchmark_run_id == run.benchmark_run_id
    stored_configs = await repository.get_configs(run.benchmark_run_id)
    assert stored_configs[0].output_mode.value == "structured_json"
    assert stored_configs[0].request_timeout_seconds == 30
    assert stored_configs[0].max_concurrent_requests == 1
    assert len(await repository.ready_outbox(NOW)) == 1
    claimed = await repository.claim(sample.benchmark_sample_id, "worker", NOW, 10)
    assert claimed is not None and claimed.status is BenchmarkSampleStatus.GENERATING

    source_hash, source_size = source_identity(SOURCE)
    artifact = GeneratedSolutionArtifact(
        benchmark_sample_id=sample.benchmark_sample_id,
        source=SOURCE,
        source_hash=source_hash,
        source_size=source_size,
        provider_response_id="fake-1",
        input_tokens=10,
        output_tokens=20,
        generation_latency_ms=7,
        created_at=NOW,
    )
    assert await repository.store_artifact(sample.benchmark_sample_id, "worker", artifact, NOW)
    assert await repository.get_artifact(sample.benchmark_sample_id) == artifact

    snapshot = snapshot_fixture(source=SOURCE, evaluation_id=sample.evaluation_id)
    assert await repository.complete(
        sample.benchmark_sample_id,
        "worker",
        snapshot,
        NOW + timedelta(seconds=1),
        1,
    )
    assert await repository.complete(
        sample.benchmark_sample_id,
        "worker",
        snapshot,
        NOW + timedelta(seconds=1),
        1,
    )
    completed = await repository.get_sample(sample.benchmark_sample_id)
    completed_run = await repository.get_run(run.benchmark_run_id)
    assert completed is not None and completed.status is BenchmarkSampleStatus.COMPLETED
    assert completed_run is not None and completed_run.status is BenchmarkRunStatus.COMPLETED
    assert await database_harness.repository.get(sample.evaluation_id) == snapshot


async def test_benchmark_run_listing_is_reverse_chronological_filtered_and_limited(
    database_harness: DatabaseHarness,
) -> None:
    repository = database_harness.benchmark_repository
    first_run, first_config, first_sample = _plan(idempotency_key=None)
    first_run = first_run.model_copy(update={"created_at": NOW - timedelta(hours=1)})
    await repository.create_plan(first_run, [first_config], [first_sample])

    second_run, second_config, second_sample = _plan(idempotency_key=None)
    second_run = second_run.model_copy(update={"created_at": NOW, "dataset_version": "2"})
    second_sample = second_sample.model_copy(update={"task_version": "2"})
    await repository.create_plan(second_run, [second_config], [second_sample])

    listed = await repository.list_runs(limit=2)
    filtered = await repository.list_runs(
        limit=20,
        dataset_id="codejudge-core",
        dataset_version="2",
    )

    assert [run.benchmark_run_id for run in listed] == [
        second_run.benchmark_run_id,
        first_run.benchmark_run_id,
    ]
    assert [run.benchmark_run_id for run in filtered] == [second_run.benchmark_run_id]
    assert len(await repository.list_runs(limit=1)) == 1


async def test_concurrent_all_failure_transitions_finalize_run(
    database_harness: DatabaseHarness,
) -> None:
    repository = database_harness.benchmark_repository
    run, config, first = _plan(idempotency_key=None)
    second = first.model_copy(
        update={
            "benchmark_sample_id": uuid4(),
            "evaluation_id": uuid4(),
            "sample_index": 2,
        }
    )
    run = run.model_copy(update={"planned_sample_count": 2})
    await repository.create_plan(run, [config], [first, second])
    assert await repository.claim(first.benchmark_sample_id, "worker-a", NOW, 10)
    assert await repository.claim(second.benchmark_sample_id, "worker-b", NOW, 10)

    statuses = await asyncio.gather(
        repository.record_failure(
            first.benchmark_sample_id,
            "worker-a",
            "malformed_provider_response",
            generation=True,
            retryable=False,
            now=NOW + timedelta(seconds=1),
            retry_base_delay_seconds=0,
        ),
        repository.record_failure(
            second.benchmark_sample_id,
            "worker-b",
            "provider_timeout",
            generation=True,
            retryable=False,
            now=NOW + timedelta(seconds=1),
            retry_base_delay_seconds=0,
        ),
    )

    finalized = await repository.get_run(run.benchmark_run_id)
    assert statuses == [
        BenchmarkSampleStatus.GENERATION_FAILED,
        BenchmarkSampleStatus.GENERATION_FAILED,
    ]
    assert finalized is not None
    assert finalized.status is BenchmarkRunStatus.COMPLETED
    assert finalized.completed_at == NOW + timedelta(seconds=1)

    async with database_harness.database.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE benchmark_runs SET status = 'running', completed_at = NULL "
                "WHERE benchmark_run_id = :run_id"
            ),
            {"run_id": run.benchmark_run_id},
        )
    assert await repository.reconcile_terminal_runs(NOW + timedelta(seconds=2)) == 1
    repaired = await repository.get_run(run.benchmark_run_id)
    assert repaired is not None
    assert repaired.status is BenchmarkRunStatus.COMPLETED
    assert repaired.completed_at == NOW + timedelta(seconds=2)


async def test_benchmark_idempotency_and_payload_conflict(
    database_harness: DatabaseHarness,
) -> None:
    repository = database_harness.benchmark_repository
    run, config, sample = _plan()
    await repository.create_plan(run, [config], [sample])
    duplicate, created = await repository.create_plan(run, [config], [sample])

    assert created is False
    assert duplicate.benchmark_run_id == run.benchmark_run_id
    with pytest.raises(IdempotencyConflictError):
        await repository.create_plan(
            run.model_copy(update={"request_fingerprint": "9" * 64}), [config], [sample]
        )

    with pytest.raises(DBAPIError, match="benchmark run identity is immutable"):
        async with database_harness.database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE benchmark_runs SET dataset_version = 'changed' "
                    "WHERE benchmark_run_id = :run_id"
                ),
                {"run_id": run.benchmark_run_id},
            )


async def test_crash_after_artifact_recovers_without_losing_generated_source(
    database_harness: DatabaseHarness,
) -> None:
    repository = database_harness.benchmark_repository
    run, config, sample = _plan(idempotency_key=None)
    await repository.create_plan(run, [config], [sample])
    await repository.claim(sample.benchmark_sample_id, "crashed", NOW, 1)
    source_hash, source_size = source_identity(SOURCE)
    artifact = GeneratedSolutionArtifact(
        benchmark_sample_id=sample.benchmark_sample_id,
        source=SOURCE,
        source_hash=source_hash,
        source_size=source_size,
        generation_latency_ms=7,
        created_at=NOW,
    )
    await repository.store_artifact(sample.benchmark_sample_id, "crashed", artifact, NOW)

    assert await repository.recover_stale(NOW + timedelta(seconds=2), 0.01) == 1
    recovered = await repository.get_sample(sample.benchmark_sample_id)

    assert recovered is not None and recovered.status is BenchmarkSampleStatus.GENERATED
    assert await repository.get_artifact(sample.benchmark_sample_id) == artifact
    reclaimed = await repository.claim(
        sample.benchmark_sample_id, "replacement", NOW + timedelta(seconds=3), 10
    )
    assert reclaimed is not None and reclaimed.status is BenchmarkSampleStatus.EVALUATING


async def test_existing_evaluation_snapshot_is_linked_without_reevaluation(
    database_harness: DatabaseHarness,
) -> None:
    repository = database_harness.benchmark_repository
    run, config, sample = _plan(idempotency_key=None)
    await repository.create_plan(run, [config], [sample])
    await repository.claim(sample.benchmark_sample_id, "worker", NOW, 10)
    source_hash, source_size = source_identity(SOURCE)
    await repository.store_artifact(
        sample.benchmark_sample_id,
        "worker",
        GeneratedSolutionArtifact(
            benchmark_sample_id=sample.benchmark_sample_id,
            source=SOURCE,
            source_hash=source_hash,
            source_size=source_size,
            generation_latency_ms=7,
            created_at=NOW,
        ),
        NOW,
    )
    snapshot = snapshot_fixture(source=SOURCE, evaluation_id=sample.evaluation_id)
    await database_harness.repository.create(snapshot)

    assert await repository.complete(sample.benchmark_sample_id, "worker", snapshot, NOW, 1)
    completed = await repository.get_sample(sample.benchmark_sample_id)
    assert completed is not None and completed.status is BenchmarkSampleStatus.COMPLETED
