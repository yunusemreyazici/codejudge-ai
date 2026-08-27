"""Service health endpoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.evaluator.engine import EvaluationEngine
from app.evaluator.models import RunnerCapability
from app.evaluator.service import EvaluationService


class HealthResponse(BaseModel):
    status: Literal["ok"]


class DatabaseCapability(BaseModel):
    configured: bool
    available: bool
    detail: str


def create_router(engine: EvaluationEngine, service: EvaluationService) -> APIRouter:
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

    return router
