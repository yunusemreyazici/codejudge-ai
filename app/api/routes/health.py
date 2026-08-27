"""Service health endpoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]


router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Check service health",
    description="Return a lightweight liveness response for the API process.",
)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
