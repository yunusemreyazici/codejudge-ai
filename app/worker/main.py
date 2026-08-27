"""Packaged CodeJudge worker and outbox-dispatcher entrypoint."""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
from contextlib import suppress
from uuid import uuid4

from app.analysis.factory import create_static_analysis_engine
from app.core.config import EvaluationMode, Settings
from app.core.logging import configure_logging
from app.db.repositories import SqlAlchemyEvaluationRepository
from app.db.session import Database
from app.evaluator.engine import EvaluationEngine
from app.evaluator.service import EvaluationService
from app.jobs.repositories import SqlAlchemyEvaluationJobRepository
from app.jobs.service import utc_now
from app.queue.outbox import OutboxPublisher
from app.queue.redis_streams import QueueUnavailableError, RedisStreamsQueue
from app.runners.factory import create_python_runner
from app.snapshots.metadata import ExecutionMetadataCollector
from app.tasks.registry import TaskRegistry
from app.worker.service import EvaluationWorker

logger = logging.getLogger(__name__)


async def run_worker(settings: Settings) -> None:
    if settings.evaluation_mode is not EvaluationMode.ASYNC:
        raise ValueError("codejudge-worker requires EVALUATION_MODE=async")
    if settings.database_url is None or settings.redis_url is None:
        raise ValueError("DATABASE_URL and REDIS_URL are required")

    database = Database(settings.database_url)
    queue = RedisStreamsQueue(settings.redis_url)
    jobs = SqlAlchemyEvaluationJobRepository(database.session_factory)
    registry = TaskRegistry.default(settings.default_execution_timeout)
    engine = EvaluationEngine(
        registry=registry,
        runners={"python": create_python_runner(settings)},
        max_code_size=settings.max_code_size,
        analysis_engine=(
            create_static_analysis_engine(settings) if settings.static_analysis_enabled else None
        ),
    )
    evaluations = SqlAlchemyEvaluationRepository(database.session_factory)
    evaluation_service = EvaluationService(
        engine=engine,
        execution_metadata=ExecutionMetadataCollector(settings),
        repository=evaluations,
    )
    publisher = OutboxPublisher(
        jobs,
        queue,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(handled_signal, stop.set)

    await queue.ensure_group()
    process_identity = f"{socket.gethostname()}-{uuid4()}"

    async def publisher_loop() -> None:
        while not stop.is_set():
            try:
                await publisher.dispatch_once()
                await jobs.recover_stale(
                    utc_now(),
                    settings.retry_base_delay_seconds,
                )
            except Exception as error:
                logger.error("worker maintenance failed error_type=%s", type(error).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.outbox_poll_interval_seconds)
            except TimeoutError:
                pass

    async def consumer_loop(slot: int) -> None:
        worker_id = f"{process_identity}-{slot}"
        worker = EvaluationWorker(
            worker_id=worker_id,
            evaluation_service=evaluation_service,
            job_repository=jobs,
            queue=queue,
            lease_seconds=settings.worker_lease_seconds,
            retry_base_delay_seconds=settings.retry_base_delay_seconds,
        )
        while not stop.is_set():
            try:
                await queue.heartbeat(worker_id, int(settings.worker_lease_seconds))
                message = await queue.reclaim(worker_id, int(settings.worker_lease_seconds * 1000))
                if message is None:
                    message = await queue.consume(worker_id)
                if message is not None:
                    await worker.process_message(message)
            except QueueUnavailableError:
                logger.warning("queue unavailable worker_id=%s", worker_id)
                await asyncio.sleep(settings.outbox_poll_interval_seconds)
            except Exception as error:
                logger.error(
                    "worker loop failed worker_id=%s error_type=%s",
                    worker_id,
                    type(error).__name__,
                )
                await asyncio.sleep(settings.outbox_poll_interval_seconds)

    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(publisher_loop())
            for slot in range(settings.worker_concurrency):
                group.create_task(consumer_loop(slot))
    finally:
        await queue.close()
        await database.dispose()


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
