"""FastAPI application factory and default ASGI application."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.ai.factory import create_ai_service
from app.ai.service import AIService
from app.analysis.factory import create_static_analysis_engine
from app.api.router import create_api_router
from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.repositories import SqlAlchemyBenchmarkRepository
from app.benchmarks.service import BenchmarkService
from app.core.config import EvaluationMode, Settings
from app.core.logging import configure_logging
from app.core.version import codejudge_version
from app.db.repositories import EvaluationRepository, SqlAlchemyEvaluationRepository
from app.db.session import Database
from app.evaluator.engine import EvaluationEngine
from app.evaluator.service import EvaluationService
from app.jobs.repositories import EvaluationJobRepository, SqlAlchemyEvaluationJobRepository
from app.jobs.service import EvaluationJobService
from app.queue.redis_streams import EvaluationQueue, RedisStreamsQueue
from app.runners.base import CodeRunner
from app.runners.factory import create_python_runner
from app.snapshots.metadata import ExecutionMetadataCollector, ExecutionMetadataProvider
from app.tasks.registry import TaskRegistry

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    registry: TaskRegistry | None = None,
    python_runner: CodeRunner | None = None,
    evaluation_repository: EvaluationRepository | None = None,
    job_repository: EvaluationJobRepository | None = None,
    evaluation_queue: EvaluationQueue | None = None,
    execution_metadata: ExecutionMetadataProvider | None = None,
    ai_service: AIService | None = None,
    benchmark_service: BenchmarkService | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    configure_logging(resolved_settings.log_level)
    resolved_registry = registry or TaskRegistry.default(
        resolved_settings.default_execution_timeout
    )
    resolved_runner = python_runner or create_python_runner(resolved_settings)
    engine = EvaluationEngine(
        registry=resolved_registry,
        runners={"python": resolved_runner},
        max_code_size=resolved_settings.max_code_size,
        analysis_engine=(
            create_static_analysis_engine(resolved_settings)
            if resolved_settings.static_analysis_enabled
            else None
        ),
    )
    database: Database | None = None
    resolved_repository = evaluation_repository
    resolved_job_repository = job_repository
    needs_database = resolved_settings.persistence_enabled and (
        resolved_repository is None
        or (resolved_settings.benchmark_enabled and benchmark_service is None)
        or (
            resolved_settings.evaluation_mode is EvaluationMode.ASYNC
            and resolved_job_repository is None
        )
    )
    if needs_database:
        if resolved_settings.database_url is None:
            raise ValueError("DATABASE_URL is required when persistence is enabled")
        database = Database(resolved_settings.database_url)
    if resolved_repository is None and database is not None:
        resolved_repository = SqlAlchemyEvaluationRepository(database.session_factory)
    if (
        resolved_job_repository is None
        and resolved_settings.evaluation_mode is EvaluationMode.ASYNC
        and database is not None
    ):
        resolved_job_repository = SqlAlchemyEvaluationJobRepository(database.session_factory)
    resolved_ai_service = ai_service or create_ai_service(resolved_settings, resolved_runner)
    owns_ai_service = ai_service is None
    service = EvaluationService(
        engine=engine,
        execution_metadata=execution_metadata or ExecutionMetadataCollector(resolved_settings),
        repository=resolved_repository,
        ai_service=resolved_ai_service,
    )
    resolved_benchmark_service = benchmark_service
    if resolved_benchmark_service is None and resolved_settings.benchmark_enabled:
        if database is None:
            raise ValueError("Benchmarking requires a PostgreSQL database")
        benchmark_repository = SqlAlchemyBenchmarkRepository(database.session_factory)
        resolved_benchmark_service = BenchmarkService(
            benchmark_repository,
            BenchmarkDatasetRegistry.default(resolved_registry),
            resolved_registry,
            service,
            max_models=resolved_settings.max_benchmark_models,
            max_tasks=resolved_settings.max_benchmark_tasks,
            max_samples_per_task=resolved_settings.max_benchmark_samples_per_task,
            max_total_generations=resolved_settings.max_benchmark_total_generations,
            max_attempts=resolved_settings.worker_max_attempts,
        )
    resolved_queue = evaluation_queue
    owns_queue = False
    if resolved_settings.evaluation_mode is EvaluationMode.ASYNC and resolved_queue is None:
        if resolved_settings.redis_url is None:
            raise ValueError("REDIS_URL is required in async evaluation mode")
        resolved_queue = RedisStreamsQueue(resolved_settings.redis_url)
        owns_queue = True
    job_service = None
    if resolved_settings.evaluation_mode is EvaluationMode.ASYNC:
        if resolved_job_repository is None or resolved_repository is None:
            raise ValueError("Async evaluation mode requires PostgreSQL repositories")
        job_service = EvaluationJobService(
            service,
            resolved_job_repository,
            resolved_repository,
            max_attempts=resolved_settings.worker_max_attempts,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if owns_queue and resolved_queue is not None:
                await resolved_queue.close()
            if owns_ai_service:
                await resolved_ai_service.close()
            if database is not None:
                await database.dispose()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=codejudge_version(),
        summary="Deterministic code evaluation and reproducible coding benchmarks",
        description=(
            "CodeJudge AI Phase 7.6: repeated-sample benchmark statistics and stability "
            "with authoritative deterministic scoring and reproducible archives."
        ),
        lifespan=lifespan,
    )
    application.include_router(
        create_api_router(
            resolved_registry,
            engine,
            service,
            job_service,
            resolved_settings.evaluation_mode,
            resolved_queue,
            resolved_benchmark_service,
        )
    )
    application.state.database = database
    application.state.evaluation_queue = resolved_queue
    application.state.ai_service = resolved_ai_service
    application.state.benchmark_service = resolved_benchmark_service

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
        logger.exception(
            "unhandled request error method=%s path=%s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    return application


app = create_app()
