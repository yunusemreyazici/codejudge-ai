"""Compose application routes."""

from fastapi import APIRouter

from app.api.routes import evaluations, health, tasks
from app.evaluator.engine import EvaluationEngine
from app.tasks.registry import TaskRegistry


def create_api_router(registry: TaskRegistry, engine: EvaluationEngine) -> APIRouter:
    router = APIRouter()
    router.include_router(health.create_router(engine))
    versioned = APIRouter(prefix="/api/v1")
    versioned.include_router(tasks.create_router(registry))
    versioned.include_router(evaluations.create_router(engine))
    router.include_router(versioned)
    return router
