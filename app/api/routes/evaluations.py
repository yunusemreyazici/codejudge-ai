"""Synchronous evaluation and immutable history endpoints."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.evaluator.engine import (
    CodeSizeExceededError,
    EvaluationInfrastructureError,
    UnsupportedLanguageError,
)
from app.evaluator.models import EvaluationRequest, EvaluationResult
from app.evaluator.service import EvaluationHistoryUnavailableError, EvaluationService
from app.snapshots.models import EvaluationDetail, EvaluationSummary
from app.tasks.registry import TaskNotFoundError


def create_router(service: EvaluationService) -> APIRouter:
    router = APIRouter(prefix="/evaluations", tags=["evaluations"])

    @router.post(
        "",
        response_model=EvaluationResult,
        response_model_exclude_none=True,
        summary="Evaluate a code submission",
        description=(
            "Run a Python submission synchronously against deterministic tests and static "
            "analysis using the configured execution backend."
        ),
        responses={
            status.HTTP_400_BAD_REQUEST: {"description": "Unsupported language"},
            status.HTTP_404_NOT_FOUND: {"description": "Task not found"},
            status.HTTP_503_SERVICE_UNAVAILABLE: {
                "description": "Evaluation execution or analysis infrastructure is unavailable"
            },
            status.HTTP_422_UNPROCESSABLE_CONTENT: {
                "description": "Invalid request or source code exceeds the configured limit"
            },
        },
    )
    async def evaluate(request: EvaluationRequest) -> EvaluationResult:
        try:
            return await service.evaluate(request)
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

    @router.get(
        "",
        response_model=list[EvaluationSummary],
        summary="List immutable evaluation snapshots",
    )
    async def list_evaluations(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        task_id: str | None = Query(default=None, min_length=1),
        language: str | None = Query(default=None, min_length=1),
        minimum_score: float | None = Query(default=None, ge=0, le=100),
        maximum_score: float | None = Query(default=None, ge=0, le=100),
    ) -> list[EvaluationSummary]:
        if (
            minimum_score is not None
            and maximum_score is not None
            and minimum_score > maximum_score
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="minimum_score must not exceed maximum_score",
            )
        try:
            return await service.list(
                limit=limit,
                offset=offset,
                task_id=None if task_id is None else task_id.strip().lower(),
                language=None if language is None else language.strip().lower(),
                minimum_score=minimum_score,
                maximum_score=maximum_score,
            )
        except EvaluationHistoryUnavailableError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(error),
            ) from error
        except EvaluationInfrastructureError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(error),
            ) from error

    @router.get(
        "/{evaluation_id}",
        response_model=EvaluationDetail,
        response_model_exclude_none=True,
        summary="Get one stored immutable evaluation snapshot",
    )
    async def get_evaluation(evaluation_id: UUID) -> EvaluationDetail:
        try:
            evaluation = await service.get(evaluation_id)
        except EvaluationHistoryUnavailableError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(error),
            ) from error
        except EvaluationInfrastructureError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(error),
            ) from error
        if evaluation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown evaluation: {evaluation_id}",
            )
        return evaluation

    return router
