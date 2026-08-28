"""Strict AI request, result, artifact, and provenance models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AIStatus(StrEnum):
    DISABLED = "disabled"
    COMPLETED = "completed"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    DISPUTED = "disputed"
    SKIPPED = "skipped"


class AIComponentStatus(StrEnum):
    COMPLETED = "completed"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


class AIFindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AIFindingCategory(StrEnum):
    REQUIREMENTS = "requirements"
    LOGIC = "logic"
    MAINTAINABILITY = "maintainability"
    EDGE_CASE = "edge_case"


class JudgeFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: AIFindingSeverity
    category: AIFindingCategory
    message: str = Field(min_length=1, max_length=1000)
    line: int | None = Field(default=None, ge=1)


class JudgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements_adherence: float = Field(ge=0, le=100)
    logic_risk: float = Field(ge=0, le=100)
    maintainability: float = Field(ge=0, le=100)
    edge_case_coverage: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    findings: list[JudgeFinding] = Field(default_factory=list, max_length=20)
    summary: str = Field(min_length=1, max_length=2000)


class ProviderUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class ProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str
    response_id: str | None = None
    usage: ProviderUsage = Field(default_factory=ProviderUsage)
    latency_ms: int = Field(ge=0)


class StructuredLLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component: str
    model: str
    system_prompt: str
    input_payload: dict[str, Any]
    response_schema: dict[str, Any]
    max_output_tokens: int = Field(gt=0)
    temperature: float = Field(ge=0, le=2)
    top_p: float = Field(gt=0, le=1)


class JudgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = "llm_judge"
    provider_id: str
    model: str
    prompt_version: str
    prompt_hash: str
    rendered_input_hash: str
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    dimensions: dict[str, float]
    findings: list[JudgeFinding]
    summary: str
    provider_response_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)
    raw_response_hash: str = Field(min_length=64, max_length=64)


class GeneratedTestProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^test_[A-Za-z0-9_]+$", min_length=6, max_length=100)
    rationale: str = Field(min_length=1, max_length=1000)
    code: str = Field(min_length=1)


class GeneratedTestsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tests: list[GeneratedTestProposal]


class AdversarialTestArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = "llm_adversarial_generator"
    name: str
    rationale: str
    code: str
    source_hash: str = Field(min_length=64, max_length=64)
    structurally_valid: bool
    reference_valid: bool
    candidate_passed: bool | None = None
    rejection_reason: str | None = None


class AdversarialResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AIComponentStatus
    reason: str | None = None
    generated: int = Field(ge=0)
    structurally_accepted: int = Field(ge=0)
    reference_valid: int = Field(ge=0)
    candidate_passed: int = Field(ge=0)
    candidate_failed: int = Field(ge=0)
    robustness_score: float | None = Field(default=None, ge=0, le=100)
    tests: list[AdversarialTestArtifact] = Field(default_factory=list)
    provider_id: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    rendered_input_hash: str | None = None
    provider_response_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    raw_response_hash: str | None = Field(default=None, min_length=64, max_length=64)


class AIProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str
    judge_prompt_version: str
    judge_prompt_hash: str
    adversarial_prompt_version: str
    adversarial_prompt_hash: str
    provider_id: str | None
    judge_models: list[str]
    adversarial_model: str | None
    temperature: float
    top_p: float
    max_output_tokens: int
    reference_fingerprint: str | None


class AIIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    policy_version: str
    judge_prompt_version: str
    judge_prompt_hash: str
    adversarial_prompt_version: str
    adversarial_prompt_hash: str
    provider_id: str | None
    judge_models: tuple[str, ...]
    adversarial_model: str | None
    temperature: float
    top_p: float
    max_output_tokens: int
    reference_fingerprint: str | None


class AIAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AIStatus
    reason: str | None = None
    ai_score: float | None = Field(default=None, ge=0, le=100)
    judge_score: float | None = Field(default=None, ge=0, le=100)
    judge_disputed: bool = False
    judge_disagreement_spread: float | None = Field(default=None, ge=0, le=100)
    judge_results: list[JudgeResult] = Field(default_factory=list)
    adversarial_tests: AdversarialResult | None = None
    provenance: AIProvenance
    ai_reproducibility_fingerprint: str = Field(min_length=64, max_length=64)
