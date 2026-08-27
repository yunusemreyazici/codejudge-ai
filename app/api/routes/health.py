"""Service health endpoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import EvaluationMode
from app.evaluator.engine import EvaluationEngine
from app.evaluator.models import RunnerCapability
from app.evaluator.service import EvaluationService
from app.queue.redis_streams import EvaluationQueue, QueueUnavailableError


class HealthResponse(BaseModel):
    status: Literal["ok"]


class DatabaseCapability(BaseModel):
    configured: bool
    available: bool
    detail: str


class QueueCapability(BaseModel):
    configured: bool
    available: bool
    active_workers: int
    detail: str


def create_router(
    engine: EvaluationEngine,
    service: EvaluationService,
    queue: EvaluationQueue | None,
    evaluation_mode: EvaluationMode,
) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get(
        "/health",
        response_model=HealthResponse,
        summary="Check service health",
        description="Return a lightweight liveness response for the API process.",
    )
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @router.get(
        "/health/sandbox",
        response_model=RunnerCapability,
        summary="Check execution backend capability",
        description="Check whether the configured Python execution backend is operational.",
    )
    async def sandbox_health() -> RunnerCapability:
        return await engine.runner_capability("python")

    @router.get(
        "/health/database",
        response_model=DatabaseCapability,
        summary="Check persistence capability",
    )
    async def database_health() -> DatabaseCapability:
        if not service.persistence_configured:
            return DatabaseCapability(
                configured=False,
                available=False,
                detail="Persistence is disabled.",
            )
        available = await service.database_available()
        return DatabaseCapability(
            configured=True,
            available=available,
            detail="PostgreSQL is available." if available else "PostgreSQL is unavailable.",
        )

    @router.get(
        "/health/queue",
        response_model=QueueCapability,
        summary="Check asynchronous queue capability",
    )
    async def queue_health() -> QueueCapability:
        if evaluation_mode is not EvaluationMode.ASYNC or queue is None:
            return QueueCapability(
                configured=False,
                available=False,
                active_workers=0,
                detail="Asynchronous evaluation mode is disabled.",
            )
        available = await queue.check_capability()
        workers = 0
        if available:
            try:
                workers = await queue.active_workers()
            except QueueUnavailableError:
                available = False
        return QueueCapability(
            configured=True,
            available=available,
            active_workers=workers,
            detail="Redis queue is available." if available else "Redis queue is unavailable.",
        )

    return router
