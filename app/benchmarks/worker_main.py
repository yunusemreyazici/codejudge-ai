"""Packaged Phase 7 benchmark worker entrypoint."""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from app.ai.factory import create_ai_service
from app.ai.providers.openai_compatible import OpenAICompatibleProvider
from app.analysis.factory import create_static_analysis_engine
from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.queue import BenchmarkOutboxPublisher, BenchmarkQueue
from app.benchmarks.repositories import SqlAlchemyBenchmarkRepository
from app.benchmarks.run_config import load_benchmark_config, resolved_provider_values
from app.benchmarks.worker import BenchmarkWorker
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.repositories import SqlAlchemyEvaluationRepository
from app.db.session import Database
from app.evaluator.engine import EvaluationEngine
from app.evaluator.service import EvaluationService
from app.jobs.service import utc_now
from app.queue.redis_streams import QueueUnavailableError
from app.runners.factory import create_python_runner
from app.snapshots.metadata import ExecutionMetadataCollector
from app.tasks.registry import TaskRegistry

logger = logging.getLogger(__name__)


async def run_benchmark_worker(settings: Settings) -> None:
    if not settings.benchmark_enabled:
        raise ValueError("codejudge-benchmark-worker requires BENCHMARK_ENABLED=true")
    if settings.database_url is None or settings.redis_url is None:
        raise ValueError("Benchmark database and Redis are required")

    database = Database(settings.database_url)
    queue = BenchmarkQueue(settings.redis_url)
    repository = SqlAlchemyBenchmarkRepository(database.session_factory)
    registry = TaskRegistry.default(settings.default_execution_timeout)
    datasets = BenchmarkDatasetRegistry.default(registry)
    runner = create_python_runner(settings)
    engine = EvaluationEngine(
        registry=registry,
        runners={"python": runner},
        max_code_size=settings.max_code_size,
        analysis_engine=(
            create_static_analysis_engine(settings) if settings.static_analysis_enabled else None
        ),
    )
    evaluations = SqlAlchemyEvaluationRepository(database.session_factory)
    ai_service = create_ai_service(settings, runner)
    evaluation_service = EvaluationService(
        engine=engine,
        execution_metadata=ExecutionMetadataCollector(settings),
        repository=evaluations,
        ai_service=ai_service,
    )
    providers: dict[str, OpenAICompatibleProvider] = {}
    if settings.benchmark_config_path is not None:
        config = load_benchmark_config(Path(settings.benchmark_config_path))
        for provider_id, (
            base_url,
            credential,
            request_timeout_seconds,
            max_concurrent_requests,
        ) in resolved_provider_values(config).items():
            providers[provider_id] = OpenAICompatibleProvider(
                base_url=base_url,
                api_key=credential,
                timeout_seconds=request_timeout_seconds,
                max_attempts=settings.llm_max_attempts,
                max_response_bytes=settings.llm_max_response_bytes,
                max_concurrent_requests=max_concurrent_requests,
            )
    elif settings.benchmark_base_url is not None and settings.benchmark_api_key is not None:
        providers[settings.benchmark_provider_id] = OpenAICompatibleProvider(
            base_url=settings.benchmark_base_url,
            api_key=settings.benchmark_api_key,
            timeout_seconds=settings.llm_timeout_seconds,
            max_attempts=settings.llm_max_attempts,
            max_response_bytes=settings.llm_max_response_bytes,
        )
    else:
        raise ValueError("Benchmark provider configuration is required")
    publisher = BenchmarkOutboxPublisher(
        repository,
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
                await repository.recover_stale(utc_now(), settings.retry_base_delay_seconds)
                await repository.reconcile_terminal_runs(utc_now())
            except Exception as error:
                logger.error("benchmark maintenance failed error_type=%s", type(error).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.outbox_poll_interval_seconds)
            except TimeoutError:
                pass

    async def consumer_loop(slot: int) -> None:
        worker_id = f"{process_identity}-{slot}"
        worker = BenchmarkWorker(
            worker_id=worker_id,
            providers=providers,
            repository=repository,
            queue=queue,
            datasets=datasets,
            tasks=registry,
            evaluations=evaluation_service,
            max_code_size=settings.max_code_size,
            lease_seconds=settings.worker_lease_seconds,
            retry_base_delay_seconds=settings.retry_base_delay_seconds,
        )
        while not stop.is_set():
            try:
                message = await queue.reclaim(worker_id, int(settings.worker_lease_seconds * 1000))
                if message is None:
                    message = await queue.consume(worker_id)
                if message is not None:
                    await worker.process_message(message)
            except QueueUnavailableError:
                await asyncio.sleep(settings.outbox_poll_interval_seconds)
            except Exception as error:
                logger.error(
                    "benchmark worker loop failed worker_id=%s error_type=%s",
                    worker_id,
                    type(error).__name__,
                )
                await asyncio.sleep(settings.outbox_poll_interval_seconds)

    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(publisher_loop())
            for slot in range(settings.benchmark_generation_concurrency):
                group.create_task(consumer_loop(slot))
    finally:
        for provider in providers.values():
            await provider.close()
        await ai_service.close()
        await queue.close()
        await database.dispose()


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    asyncio.run(run_benchmark_worker(settings))


if __name__ == "__main__":
    main()
