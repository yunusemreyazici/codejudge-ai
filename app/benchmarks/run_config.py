"""Commit-safe Phase 7.2 benchmark configuration and cost preflight."""

from __future__ import annotations

import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.ai.prompts import canonical_json
from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.benchmarks.models import BenchmarkModelRequest, PricingSnapshot
from app.benchmarks.pricing import PricingCatalog
from app.benchmarks.prompts import CODING_SYSTEM_PROMPT, coding_payload
from app.core.config import (
    DEFAULT_MAX_BENCHMARK_MODELS,
    DEFAULT_MAX_BENCHMARK_SAMPLES_PER_TASK,
    DEFAULT_MAX_BENCHMARK_TASKS,
    DEFAULT_MAX_BENCHMARK_TOTAL_GENERATIONS,
)
from app.tasks.registry import TaskRegistry

_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class BenchmarkConfigError(ValueError):
    """A safe configuration or preflight failure."""


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: Literal["openai-compatible"] = "openai-compatible"
    base_url_env: str = Field(min_length=1, max_length=255)
    credential_env: str = Field(min_length=1, max_length=255)

    @field_validator("base_url_env", "credential_env")
    @classmethod
    def environment_name(cls, value: str) -> str:
        if not _ENVIRONMENT_NAME.fullmatch(value):
            raise ValueError("must be an uppercase environment variable name")
        return value


class DatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class AIEvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class PricingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1, max_length=255)
    currency: str = Field(min_length=3, max_length=3)
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    def snapshot(self) -> PricingSnapshot:
        return PricingSnapshot(
            pricing_version=self.version,
            input_cost_per_million_tokens=self.input_per_million,
            output_cost_per_million_tokens=self.output_per_million,
            currency=self.currency,
        )


class BenchmarkRunConfig(BaseModel):
    """Repository-friendly input; it contains identities and env names, never secrets."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    name: str = Field(min_length=1, max_length=255)
    dataset: DatasetConfig
    samples_per_task: int = Field(default=1, ge=1)
    models: tuple[BenchmarkModelRequest, ...] = Field(min_length=1)
    providers: dict[str, ProviderConfig] = Field(min_length=1)
    ai_evaluation: AIEvaluationConfig = AIEvaluationConfig()
    pricing: dict[str, PricingConfig] = Field(default_factory=dict)
    max_generation_cost: BudgetConfig | None = None

    @model_validator(mode="after")
    def identities_are_resolvable(self) -> BenchmarkRunConfig:
        invalid_providers = sorted(
            provider_id
            for provider_id in self.providers
            if not provider_id.strip() or "/" in provider_id
        )
        if invalid_providers:
            raise ValueError(
                "provider IDs must be nonblank and must not contain '/': "
                + ", ".join(invalid_providers)
            )
        missing = sorted({model.provider_id for model in self.models} - self.providers.keys())
        if missing:
            raise ValueError(f"models reference unregistered providers: {', '.join(missing)}")
        identities = [
            (
                model.provider_id,
                model.model,
                model.temperature,
                model.top_p,
                model.max_output_tokens,
                model.seed,
            )
            for model in self.models
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate model configurations are not allowed")
        for key in self.pricing:
            provider_id, separator, model = key.partition("/")
            if not separator or not provider_id or not model:
                raise ValueError(f"pricing key must be provider-id/model: {key}")
            if provider_id not in self.providers:
                raise ValueError(f"pricing references unregistered provider: {provider_id}")
            if (provider_id, model) not in {
                (request.provider_id, request.model) for request in self.models
            }:
                raise ValueError(f"pricing references an unconfigured model: {key}")
        return self

    def pricing_catalog(self) -> PricingCatalog:
        entries: dict[tuple[str, str], PricingSnapshot] = {}
        for key, pricing in self.pricing.items():
            provider_id, _, model = key.partition("/")
            entries[(provider_id, model)] = pricing.snapshot()
        return PricingCatalog(entries)


class PlannedModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    model: str
    planned_generations: int
    maximum_input_tokens: int
    maximum_output_tokens: int
    pricing_version: str | None = None
    estimated_maximum_cost: Decimal | None = None
    currency: str | None = None
    credential_configured: bool
    endpoint_configured: bool


class BenchmarkPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    dataset_id: str
    dataset_version: str
    dataset_fingerprint: str
    task_count: int
    model_count: int
    samples_per_task: int
    planned_generations: int
    ai_evaluation_enabled: bool
    models: tuple[PlannedModel, ...]
    estimated_maximum_costs: dict[str, Decimal]
    unknown_pricing: tuple[str, ...]
    warnings: tuple[str, ...]
    estimate_basis: str


def load_benchmark_config(path: Path) -> BenchmarkRunConfig:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise BenchmarkConfigError(f"Unable to read benchmark configuration: {path}") from error
    if not isinstance(payload, dict):
        raise BenchmarkConfigError("Benchmark configuration must be a YAML mapping.")
    try:
        return BenchmarkRunConfig.model_validate(payload)
    except ValidationError as error:
        raise BenchmarkConfigError(_validation_message(error)) from error


def build_plan(
    config: BenchmarkRunConfig,
    *,
    tasks: TaskRegistry | None = None,
    datasets: BenchmarkDatasetRegistry | None = None,
    environment: dict[str, str] | None = None,
    max_models: int = DEFAULT_MAX_BENCHMARK_MODELS,
    max_tasks: int = DEFAULT_MAX_BENCHMARK_TASKS,
    max_samples_per_task: int = DEFAULT_MAX_BENCHMARK_SAMPLES_PER_TASK,
    max_total_generations: int = DEFAULT_MAX_BENCHMARK_TOTAL_GENERATIONS,
) -> BenchmarkPlan:
    resolved_tasks = tasks or TaskRegistry.default()
    resolved_datasets = datasets or BenchmarkDatasetRegistry.default(resolved_tasks)
    dataset = resolved_datasets.get(config.dataset.id, config.dataset.version)
    task_count = len(dataset.task_entries)
    planned = task_count * len(config.models) * config.samples_per_task
    _validate_limits(
        config,
        task_count,
        planned,
        max_models=max_models,
        max_tasks=max_tasks,
        max_samples_per_task=max_samples_per_task,
        max_total_generations=max_total_generations,
    )
    env = dict(os.environ) if environment is None else environment
    task_input_bounds = {
        entry.task_id: _input_token_bound(resolved_tasks, entry.task_id)
        for entry in dataset.task_entries
    }
    catalog = config.pricing_catalog()
    planned_models: list[PlannedModel] = []
    totals: dict[str, Decimal] = {}
    unknown: list[str] = []
    warnings: list[str] = []
    for model in config.models:
        provider = config.providers[model.provider_id]
        endpoint = env.get(provider.base_url_env, "").strip()
        credential = env.get(provider.credential_env, "").strip()
        if endpoint and not _valid_http_url(endpoint):
            raise BenchmarkConfigError(
                f"{provider.base_url_env} must contain an http or https URL."
            )
        if not endpoint:
            warnings.append(f"Provider endpoint is not configured for {model.provider_id}.")
        if not credential:
            warnings.append(f"Provider credential is not configured for {model.provider_id}.")
        pricing = catalog.get(model.provider_id, model.model)
        maximum_input = sum(task_input_bounds.values()) * config.samples_per_task
        maximum_output = task_count * config.samples_per_task * model.max_output_tokens
        estimate = _maximum_cost(pricing, maximum_input, maximum_output)
        identity = f"{model.provider_id}/{model.model}"
        if estimate is None:
            unknown.append(identity)
            warnings.append(f"Pricing is unknown for {identity}; cost is not treated as zero.")
        elif pricing is not None:
            totals[pricing.currency] = totals.get(pricing.currency, Decimal()) + estimate
        planned_models.append(
            PlannedModel(
                provider_id=model.provider_id,
                model=model.model,
                planned_generations=task_count * config.samples_per_task,
                maximum_input_tokens=maximum_input,
                maximum_output_tokens=maximum_output,
                pricing_version=None if pricing is None else pricing.pricing_version,
                estimated_maximum_cost=estimate,
                currency=None if pricing is None else pricing.currency,
                credential_configured=bool(credential),
                endpoint_configured=bool(endpoint),
            )
        )
    _validate_budget(config, totals, unknown)
    if config.ai_evaluation.enabled:
        warnings.append("AI evaluation adds separate provider cost not included in this estimate.")
    return BenchmarkPlan(
        name=config.name,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        dataset_fingerprint=dataset.dataset_fingerprint,
        task_count=task_count,
        model_count=len(config.models),
        samples_per_task=config.samples_per_task,
        planned_generations=planned,
        ai_evaluation_enabled=config.ai_evaluation.enabled,
        models=tuple(planned_models),
        estimated_maximum_costs=dict(sorted(totals.items())),
        unknown_pricing=tuple(unknown),
        warnings=tuple(dict.fromkeys(warnings)),
        estimate_basis=(
            "estimate: UTF-8 bytes for the versioned system prompt and canonical public task "
            "payload are conservatively counted as input tokens; configured max_output_tokens "
            "is used for every planned generation"
        ),
    )


def validate_run_preflight(
    config: BenchmarkRunConfig,
    plan: BenchmarkPlan,
    *,
    ai_enabled: bool,
) -> None:
    missing = [
        f"{model.provider_id}/{model.model}"
        for model in plan.models
        if not model.credential_configured or not model.endpoint_configured
    ]
    if missing:
        raise BenchmarkConfigError(
            "Provider endpoint or credential is missing for: " + ", ".join(missing)
        )
    if config.ai_evaluation.enabled != ai_enabled:
        expected = "enabled" if config.ai_evaluation.enabled else "disabled"
        raise BenchmarkConfigError(
            f"Configuration requires AI evaluation {expected}, but runtime settings differ."
        )


def resolved_provider_values(
    config: BenchmarkRunConfig, environment: dict[str, str] | None = None
) -> dict[str, tuple[str, str]]:
    """Resolve secrets only at worker construction; callers must never log the return value."""
    env = dict(os.environ) if environment is None else environment
    resolved: dict[str, tuple[str, str]] = {}
    selected_provider_ids = {model.provider_id for model in config.models}
    for provider_id in sorted(selected_provider_ids):
        provider = config.providers[provider_id]
        base_url = env.get(provider.base_url_env, "").strip().rstrip("/")
        credential = env.get(provider.credential_env, "").strip()
        if not base_url or not credential or not _valid_http_url(base_url):
            raise BenchmarkConfigError(f"Provider configuration is incomplete for {provider_id}.")
        resolved[provider_id] = (base_url, credential)
    return resolved


def _input_token_bound(tasks: TaskRegistry, task_id: str) -> int:
    task = tasks.get(task_id).specification
    public_payload = canonical_json(coding_payload(task))
    return len(CODING_SYSTEM_PROMPT.encode("utf-8")) + len(public_payload.encode("utf-8")) + 512


def _maximum_cost(
    pricing: PricingSnapshot | None, maximum_input: int, maximum_output: int
) -> Decimal | None:
    if pricing is None:
        return None
    million = Decimal(1_000_000)
    return (
        Decimal(maximum_input) * pricing.input_cost_per_million_tokens / million
        + Decimal(maximum_output) * pricing.output_cost_per_million_tokens / million
    ).quantize(Decimal("0.000000000001"))


def _validate_limits(
    config: BenchmarkRunConfig,
    task_count: int,
    planned: int,
    *,
    max_models: int,
    max_tasks: int,
    max_samples_per_task: int,
    max_total_generations: int,
) -> None:
    if len(config.models) > max_models:
        raise BenchmarkConfigError(f"Benchmark model count exceeds {max_models}.")
    if task_count > max_tasks:
        raise BenchmarkConfigError(f"Benchmark task count exceeds {max_tasks}.")
    if config.samples_per_task > max_samples_per_task:
        raise BenchmarkConfigError(f"samples_per_task exceeds {max_samples_per_task}.")
    if planned > max_total_generations:
        raise BenchmarkConfigError(f"Planned generation count exceeds {max_total_generations}.")


def _validate_budget(
    config: BenchmarkRunConfig,
    totals: dict[str, Decimal],
    unknown: list[str],
) -> None:
    budget = config.max_generation_cost
    if budget is None:
        return
    if unknown:
        raise BenchmarkConfigError(
            "Cannot enforce max_generation_cost while pricing is unknown for: " + ", ".join(unknown)
        )
    foreign = sorted(set(totals) - {budget.currency})
    if foreign:
        raise BenchmarkConfigError(
            "Cannot compare the budget without currency conversion; found: " + ", ".join(foreign)
        )
    estimate = totals.get(budget.currency, Decimal())
    if estimate > budget.amount:
        raise BenchmarkConfigError(
            f"Estimated maximum generation cost {budget.currency} {estimate} exceeds budget "
            f"{budget.currency} {budget.amount}."
        )


def _valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _validation_message(error: ValidationError) -> str:
    messages = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"])
        messages.append(f"{location}: {item['msg']}")
    return "Invalid benchmark configuration: " + "; ".join(messages)
