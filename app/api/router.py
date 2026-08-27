"""Compose application routes."""

from fastapi import APIRouter

from app.api.routes import evaluations, health, tasks
from app.evaluator.engine import EvaluationEngine
from app.evaluator.service import EvaluationService
from app.tasks.registry import TaskRegistry


def create_api_router(
    registry: TaskRegistry,
    engine: EvaluationEngine,
    service: EvaluationService,
) -> APIRouter:
    router = APIRouter()
    router.include_router(health.create_router(engine, service))
    versioned = APIRouter(prefix="/api/v1")
    versioned.include_router(tasks.create_router(registry))
    versioned.include_router(evaluations.create_router(service))
    router.include_router(versioned)
    return router
