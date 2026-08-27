"""Synchronous code evaluation endpoint."""

from fastapi import APIRouter, HTTPException, status

from app.evaluator.engine import (
    CodeSizeExceededError,
    EvaluationEngine,
    EvaluationInfrastructureError,
    UnsupportedLanguageError,
)
from app.evaluator.models import EvaluationRequest, EvaluationResult
from app.tasks.registry import TaskNotFoundError


def create_router(engine: EvaluationEngine) -> APIRouter:
    router = APIRouter(prefix="/evaluations", tags=["evaluations"])

    @router.post(
        "",
        response_model=EvaluationResult,
        summary="Evaluate a code submission",
        description=(
            "Run a Python submission synchronously against a task's deterministic pytest suite "
            "using the configured execution backend."
        ),
        responses={
            status.HTTP_400_BAD_REQUEST: {"description": "Unsupported language"},
            status.HTTP_404_NOT_FOUND: {"description": "Task not found"},
            status.HTTP_503_SERVICE_UNAVAILABLE: {
                "description": "Configured execution backend is unavailable"
            },
            status.HTTP_422_UNPROCESSABLE_CONTENT: {
                "description": "Invalid request or source code exceeds the configured limit"
            },
        },
    )
    async def evaluate(request: EvaluationRequest) -> EvaluationResult:
        try:
            return await engine.evaluate(request)
        except TaskNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown task: {request.task_id}",
            ) from error
        except UnsupportedLanguageError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
        except CodeSizeExceededError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error
        except EvaluationInfrastructureError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(error),
            ) from error

    return router
