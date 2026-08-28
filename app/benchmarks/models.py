"""Typed benchmark configuration, lifecycle, artifact, and API models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

BENCHMARK_POLICY_VERSION = "1"
CODING_PROMPT_VERSION = "1"


class BenchmarkRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class BenchmarkSampleStatus(StrEnum):
    QUEUED = "queued"
    GENERATING = "generating"
    GENERATED = "generated"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    GENERATION_FAILED = "generation_failed"
    EVALUATION_FAILED = "evaluation_failed"
    SKIPPED = "skipped"


TERMINAL_SAMPLE_STATUSES = frozenset(
    {
        BenchmarkSampleStatus.COMPLETED,
        BenchmarkSampleStatus.GENERATION_FAILED,
        BenchmarkSampleStatus.EVALUATION_FAILED,
        BenchmarkSampleStatus.SKIPPED,
    }
)


class GenerationFailureCode(StrEnum):
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_REFUSAL = "provider_refusal"
    MALFORMED_OUTPUT = "malformed_output"
    OUTPUT_TOO_LARGE = "output_too_large"
    PROVIDER_REQUEST_REJECTED = "provider_request_rejected"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"


class DatasetTaskEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    task_version: str
    task_fingerprint: str = Field(min_length=64, max_length=64)
    tests_fingerprint: str = Field(min_length=64, max_length=64)
    weight: float = Field(default=1.0, gt=0)


class BenchmarkDataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str
    dataset_version: str
    title: str
    description: str
    task_entries: tuple[DatasetTaskEntry, ...]
    dataset_fingerprint: str = Field(min_length=64, max_length=64)


class PricingSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pricing_version: str
    input_cost_per_million_tokens: Decimal = Field(ge=0)
    output_cost_per_million_tokens: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class BenchmarkModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    temperature: float = Field(default=0, ge=0, le=2)
    top_p: float = Field(default=1, gt=0, le=1)
    max_output_tokens: int = Field(default=4000, gt=0, le=100_000)
    seed: int | None = None


class BenchmarkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    models: list[BenchmarkModelRequest] = Field(min_length=1)
    samples_per_task: int = Field(default=1, ge=1)


class BenchmarkModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_config_id: UUID
    benchmark_run_id: UUID
    ordinal: int = Field(ge=0)
    provider_id: str
    model: str
    display_name: str
    temperature: float
    top_p: float
    max_output_tokens: int
    seed: int | None = None
    coding_prompt_hash: str
    model_configuration_fingerprint: str
    pricing: PricingSnapshot | None = None


class BenchmarkRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    benchmark_run_id: UUID
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: BenchmarkRunStatus
    dataset_id: str
    dataset_version: str
    dataset_fingerprint: str
    benchmark_policy_version: str
    coding_prompt_version: str
    coding_prompt_hash: str
    evaluator_fingerprint: str
    benchmark_run_fingerprint: str
    samples_per_task: int
    planned_sample_count: int
    request_fingerprint: str
    idempotency_key: str | None = None
    model_configs: tuple[BenchmarkModelConfig, ...] = ()


class BenchmarkSample(BaseModel):
    model_config = ConfigDict(frozen=True)

    benchmark_sample_id: UUID
    benchmark_run_id: UUID
    model_config_id: UUID
    evaluation_id: UUID
    task_id: str
    task_version: str
    task_fingerprint: str
    tests_fingerprint: str
    task_weight: float
    sample_index: int = Field(ge=1)
    status: BenchmarkSampleStatus
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(gt=0)
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    failure_code: str | None = None
    evaluation_duration_seconds: float | None = Field(default=None, ge=0)
    total_duration_seconds: float | None = Field(default=None, ge=0)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class GeneratedSolutionArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    benchmark_sample_id: UUID
    source: str
    source_hash: str = Field(min_length=64, max_length=64)
    source_size: int = Field(gt=0)
    generation_attempts: int = Field(default=1, ge=1)
    provider_response_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    generation_latency_ms: int = Field(ge=0)
    pricing_version: str | None = None
    generation_cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    created_at: datetime


class CodingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["python"]
    source: str = Field(min_length=1)

    @field_validator("source")
    @classmethod
    def reject_blank_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source must not be blank")
        return value


class BenchmarkAccepted(BaseModel):
    benchmark_run_id: UUID
    status: BenchmarkRunStatus
    planned_samples: int
    status_url: str


class BenchmarkRunSummary(BaseModel):
    benchmark_run_id: UUID
    status: BenchmarkRunStatus
    dataset_id: str
    dataset_version: str
    dataset_fingerprint: str
    benchmark_run_fingerprint: str
    benchmark_policy_version: str
    coding_prompt_version: str
    coding_prompt_hash: str
    evaluator_fingerprint: str
    samples_per_task: int
    planned_samples: int
    completed_samples: int
    generation_failures: int
    evaluation_failures: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    models: list[BenchmarkModelConfig]


class BenchmarkSampleSummary(BaseModel):
    benchmark_sample_id: UUID
    model_config_id: UUID
    model: str
    task_id: str
    sample_index: int
    status: BenchmarkSampleStatus
    deterministic_score: float | None = None
    ai_score: float | None = None
    generation_latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    generation_cost: Decimal | None = None
    currency: str | None = None
    evaluation_id: UUID | None = None
    failure_code: str | None = None


class BenchmarkSampleDetail(BenchmarkSampleSummary):
    provider_id: str
    model_configuration_fingerprint: str
    source: str | None = None
    source_hash: str | None = None
    source_size: int | None = None
    provider_response_id: str | None = None
    pricing_version: str | None = None
    generation_attempts: int
    generation_parameters: dict[str, int | float | None]
    evaluation_duration_seconds: float | None = None
    total_duration_seconds: float | None = None


class MetricSummary(BaseModel):
    count: int = Field(ge=0)
    mean: float | None = None
    median: float | None = None
    standard_deviation: float | None = None
    minimum: float | None = None
    maximum: float | None = None


class PerTaskMetrics(BaseModel):
    task_id: str
    sample_count: int
    generation_failures: int
    evaluation_failures: int
    scores: MetricSummary
    best_score: float | None = None
    worst_score: float | None = None
    pass_rate: float | None = None


class LeaderboardEntry(BaseModel):
    rank: int = Field(ge=1)
    model_config_id: UUID
    provider_id: str
    model: str
    display_name: str
    model_configuration_fingerprint: str
    weighted_mean_score: float | None = None
    deterministic_scores: MetricSummary
    coverage: float = Field(ge=0, le=1)
    pass_rate: float | None = Field(default=None, ge=0, le=1)
    successful_generation_rate: float = Field(ge=0, le=1)
    evaluation_completion_rate: float = Field(ge=0, le=1)
    generation_failure_rate: float = Field(ge=0, le=1)
    mean_ai_score: float | None = None
    ai_coverage: float = Field(ge=0, le=1)
    mean_judge_score: float | None = None
    mean_adversarial_robustness: float | None = None
    disputed_rate: float | None = None
    ai_unavailable_rate: float | None = None
    generation_costs: dict[str, Decimal]
    mean_generation_latency_ms: float | None = None
    median_generation_latency_ms: float | None = None
    p95_generation_latency_ms: float | None = None
    mean_evaluation_latency_seconds: float | None = None
    per_task: list[PerTaskMetrics]


class BenchmarkComparisonRequest(BaseModel):
    run_ids: list[UUID] = Field(min_length=2, max_length=10)

    @field_validator("run_ids")
    @classmethod
    def require_distinct_runs(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("run_ids must be distinct")
        return value


class BenchmarkComparison(BaseModel):
    compatible: bool
    differences: list[str]
    run_fingerprints: dict[UUID, str]
    leaderboards: dict[UUID, list[LeaderboardEntry]] | None = None
