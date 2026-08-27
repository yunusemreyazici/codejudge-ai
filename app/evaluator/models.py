"""Typed public and internal models for code evaluation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EvaluationStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class FindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class FindingCategory(StrEnum):
    VALIDATION = "validation"
    EXECUTION = "execution"
    TESTING = "testing"


class Task(BaseModel):
    """Public task specification. Test locations and sources are intentionally absent."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    language: str = Field(min_length=1)
    entrypoint: str | None = None
    timeout_seconds: float = Field(gt=0)
    version: str = "1.0"


class EvaluationRequest(BaseModel):
    task_id: str = Field(min_length=1)
    language: str = Field(min_length=1)
    code: str = Field(min_length=1)

    @field_validator("task_id", "language")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("code")
    @classmethod
    def reject_blank_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("code must not be blank")
        return value


class TestResult(BaseModel):
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    total: int = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    timed_out: bool = False


class Finding(BaseModel):
    severity: FindingSeverity
    category: FindingCategory
    message: str


class ScoreBreakdown(BaseModel):
    correctness: float = Field(ge=0, le=100)


class EvaluationResult(BaseModel):
    task_id: str
    status: EvaluationStatus
    score: float = Field(ge=0, le=100)
    tests: TestResult
    score_breakdown: ScoreBreakdown
    findings: list[Finding] = Field(default_factory=list)


class RunnerResult(BaseModel):
    """Structured output produced by a language runner."""

    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    total: int = Field(ge=0)
    timed_out: bool = False
    infrastructure_error: str | None = None
