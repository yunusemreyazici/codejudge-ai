"""FastAPI application factory and default ASGI application."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.router import create_api_router
from app.core.config import Settings
from app.core.logging import configure_logging
from app.evaluator.engine import EvaluationEngine
from app.runners.python_runner import PythonRunner
from app.tasks.registry import TaskRegistry

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    registry: TaskRegistry | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    configure_logging(resolved_settings.log_level)
    resolved_registry = registry or TaskRegistry.default(
        resolved_settings.default_execution_timeout
    )
    engine = EvaluationEngine(
        registry=resolved_registry,
        runners={"python": PythonRunner()},
        max_code_size=resolved_settings.max_code_size,
    )
    application = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        summary="Deterministic code submission evaluation",
        description=(
            "Phase 1 of CodeJudge AI: a synchronous evaluator backed by local pytest "
            "subprocesses. Local execution is not a security sandbox."
        ),
    )
    application.include_router(create_api_router(resolved_registry, engine))

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
