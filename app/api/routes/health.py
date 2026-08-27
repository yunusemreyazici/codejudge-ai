"""Service health endpoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.evaluator.engine import EvaluationEngine
from app.evaluator.models import RunnerCapability


class HealthResponse(BaseModel):
    status: Literal["ok"]


def create_router(engine: EvaluationEngine) -> APIRouter:
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

    return router
