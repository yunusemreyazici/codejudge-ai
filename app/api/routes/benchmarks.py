"""Phase 7 benchmark planning, samples, leaderboards, and comparisons."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Response, status

from app.benchmarks.datasets import DatasetNotFoundError, DatasetRegistryError
from app.benchmarks.models import (
    BenchmarkAccepted,
    BenchmarkComparison,
    BenchmarkComparisonRequest,
    BenchmarkCreateRequest,
    BenchmarkRunSummary,
    BenchmarkSampleDetail,
    BenchmarkSampleStatus,
    BenchmarkSampleSummary,
    LeaderboardEntry,
)
from app.benchmarks.service import (
    BenchmarkError,
    BenchmarkLimitError,
    BenchmarkNotFoundError,
    BenchmarkService,
)
from app.jobs.repositories import IdempotencyConflictError


def create_router(service: BenchmarkService) -> APIRouter:
    router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])

    @router.post("", response_model=BenchmarkAccepted, status_code=status.HTTP_202_ACCEPTED)
    async def create_benchmark(
        request: BenchmarkCreateRequest,
        response: Response,
        idempotency_key: str | None = Header(default=None, min_length=1, max_length=255),
    ) -> BenchmarkAccepted:
        try:
            accepted = await service.create(request, idempotency_key)
            response.status_code = status.HTTP_202_ACCEPTED
            return accepted
        except DatasetNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except (BenchmarkLimitError, DatasetRegistryError) as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        except IdempotencyConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except BenchmarkError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
            ) from error

    @router.post("/compare", response_model=BenchmarkComparison, response_model_exclude_none=True)
    async def compare_runs(request: BenchmarkComparisonRequest) -> BenchmarkComparison:
        try:
            return await service.compare(request.run_ids)
        except BenchmarkNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except BenchmarkError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
            ) from error

    @router.get("/{run_id}", response_model=BenchmarkRunSummary)
    async def get_benchmark(run_id: UUID) -> BenchmarkRunSummary:
        try:
            return await service.get(run_id)
        except BenchmarkNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except BenchmarkError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
            ) from error

    @router.get("/{run_id}/samples", response_model=list[BenchmarkSampleSummary])
    async def list_samples(
        run_id: UUID,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        model: str | None = Query(default=None, min_length=1),
        task: str | None = Query(default=None, min_length=1),
        sample_status: Annotated[BenchmarkSampleStatus | None, Query(alias="status")] = None,
    ) -> list[BenchmarkSampleSummary]:
        try:
            return await service.samples(
                run_id,
                limit=limit,
                offset=offset,
                model=model,
                task_id=task,
                status=sample_status,
            )
        except BenchmarkNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except BenchmarkError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
            ) from error

    @router.get(
        "/{run_id}/samples/{sample_id}",
        response_model=BenchmarkSampleDetail,
        response_model_exclude_none=True,
    )
    async def get_sample(run_id: UUID, sample_id: UUID) -> BenchmarkSampleDetail:
        try:
            return await service.sample_detail(run_id, sample_id)
        except BenchmarkNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except BenchmarkError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
            ) from error

    @router.get("/{run_id}/leaderboard", response_model=list[LeaderboardEntry])
    async def leaderboard(run_id: UUID) -> list[LeaderboardEntry]:
        try:
            return await service.leaderboard(run_id)
        except BenchmarkNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except BenchmarkError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
            ) from error

    return router
