"""FastAPI application factory and default ASGI application."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.analysis.factory import create_static_analysis_engine
from app.api.router import create_api_router
from app.core.config import Settings
from app.core.logging import configure_logging
from app.core.version import codejudge_version
from app.db.repositories import EvaluationRepository, SqlAlchemyEvaluationRepository
from app.db.session import Database
from app.evaluator.engine import EvaluationEngine
from app.evaluator.service import EvaluationService
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
    execution_metadata: ExecutionMetadataProvider | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    configure_logging(resolved_settings.log_level)
    resolved_registry = registry or TaskRegistry.default(
        resolved_settings.default_execution_timeout
    )
    engine = EvaluationEngine(
        registry=resolved_registry,
        runners={"python": python_runner or create_python_runner(resolved_settings)},
        max_code_size=resolved_settings.max_code_size,
        analysis_engine=(
            create_static_analysis_engine(resolved_settings)
            if resolved_settings.static_analysis_enabled
            else None
        ),
    )
    database: Database | None = None
    resolved_repository = evaluation_repository
    if resolved_repository is None and resolved_settings.persistence_enabled:
        if resolved_settings.database_url is None:
            raise ValueError("DATABASE_URL is required when persistence is enabled")
        database = Database(resolved_settings.database_url)
        resolved_repository = SqlAlchemyEvaluationRepository(database.session_factory)
    service = EvaluationService(
        engine=engine,
        execution_metadata=execution_metadata or ExecutionMetadataCollector(resolved_settings),
        repository=resolved_repository,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if database is not None:
                await database.dispose()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=codejudge_version(),
        summary="Deterministic code evaluation with immutable history",
        description=(
            "CodeJudge AI Phase 4: deterministic execution and analysis with immutable, "
            "reproducible PostgreSQL snapshots."
        ),
        lifespan=lifespan,
    )
    application.include_router(create_api_router(resolved_registry, engine, service))
    application.state.database = database

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
