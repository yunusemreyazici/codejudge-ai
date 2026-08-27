"""Compose application routes."""

from fastapi import APIRouter

from app.api.routes import evaluations, health, tasks
from app.core.config import EvaluationMode
from app.evaluator.engine import EvaluationEngine
from app.evaluator.service import EvaluationService
from app.jobs.service import EvaluationJobService
from app.queue.redis_streams import EvaluationQueue
from app.tasks.registry import TaskRegistry


def create_api_router(
    registry: TaskRegistry,
    engine: EvaluationEngine,
    service: EvaluationService,
    job_service: EvaluationJobService | None,
    evaluation_mode: EvaluationMode,
    queue: EvaluationQueue | None,
) -> APIRouter:
    router = APIRouter()
    router.include_router(health.create_router(engine, service, queue, evaluation_mode))
    versioned = APIRouter(prefix="/api/v1")
    versioned.include_router(tasks.create_router(registry))
    versioned.include_router(evaluations.create_router(service, job_service, evaluation_mode))
    router.include_router(versioned)
    return router
