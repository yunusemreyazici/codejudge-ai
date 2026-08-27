"""Typed public and internal models for code evaluation."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

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
    RESOURCE = "resource"
    SANDBOX = "sandbox"
    QUALITY = "quality"
    TYPE_SAFETY = "type_safety"
    SECURITY = "security"
    COMPLEXITY = "complexity"


class AnalysisTool(StrEnum):
    RUFF = "ruff"
    MYPY = "mypy"
    BANDIT = "bandit"
    RADON = "radon"


class FindingConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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
    tool: AnalysisTool | None = None
    code: str | None = None
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    end_column: int | None = Field(default=None, ge=1)
    fixable: bool | None = None
    confidence: FindingConfidence | None = None


class ComplexityMetrics(BaseModel):
    maximum: int = Field(ge=0)
    average: float = Field(ge=0)
    blocks: int = Field(ge=0)
    analyzable: bool = True


class StaticAnalysisResult(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    complexity: ComplexityMetrics


class ScoreBreakdown(BaseModel):
    correctness: float = Field(ge=0, le=100)
    code_quality: float | None = Field(default=None, ge=0, le=100)
    type_safety: float | None = Field(default=None, ge=0, le=100)
    security: float | None = Field(default=None, ge=0, le=100)
    complexity: float | None = Field(default=None, ge=0, le=100)


class EvaluationResult(BaseModel):
    evaluation_id: UUID | None = None
    created_at: datetime | None = None
    task_id: str
    status: EvaluationStatus
    score: float = Field(ge=0, le=100)
    tests: TestResult
    score_breakdown: ScoreBreakdown
    analysis: StaticAnalysisResult | None = None
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
    enforced_timeout_seconds: float | None = Field(default=None, gt=0)
    output_truncated: bool = False
    oom_killed: bool = False
    syntax_error: bool = False
    import_error: bool = False
    sandbox_error: str | None = None
    infrastructure_error: str | None = None


class RunnerCapability(BaseModel):
    """Operational availability of a configured execution backend."""

    backend: str
    available: bool
    detail: str
