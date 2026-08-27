"""Public task discovery endpoints."""

from fastapi import APIRouter, HTTPException, status

from app.evaluator.models import Task
from app.tasks.registry import TaskNotFoundError, TaskRegistry


def create_router(registry: TaskRegistry) -> APIRouter:
    router = APIRouter(prefix="/tasks", tags=["tasks"])

    @router.get(
        "",
        response_model=list[Task],
        summary="List coding tasks",
        description="List public task specifications. Evaluation test source is never returned.",
    )
    async def list_tasks() -> list[Task]:
        return registry.list()

    @router.get(
        "/{task_id}",
        response_model=Task,
        summary="Get a coding task",
        description="Return public metadata and the candidate-facing specification for one task.",
        responses={status.HTTP_404_NOT_FOUND: {"description": "Task not found"}},
    )
    async def get_task(task_id: str) -> Task:
        try:
            return registry.get(task_id).specification
        except TaskNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown task: {task_id}",
            ) from error

    return router
