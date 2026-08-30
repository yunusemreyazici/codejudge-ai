"""Typed application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum

DEFAULT_APP_NAME = "CodeJudge AI"
DEFAULT_APP_ENV = "development"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_EXECUTION_TIMEOUT = 5.0
DEFAULT_MAX_CODE_SIZE = 100 * 1024
DEFAULT_EXECUTION_BACKEND = "docker"
DEFAULT_SANDBOX_IMAGE = "codejudge-python-sandbox:phase2"
DEFAULT_SANDBOX_MEMORY_MB = 256
DEFAULT_SANDBOX_CPUS = 0.5
DEFAULT_SANDBOX_PIDS_LIMIT = 64
DEFAULT_SANDBOX_TIMEOUT_SECONDS = 5.0
DEFAULT_SANDBOX_OUTPUT_LIMIT_BYTES = 1024 * 1024
DEFAULT_STATIC_ANALYSIS_ENABLED = True
DEFAULT_STATIC_ANALYSIS_TIMEOUT_SECONDS = 5.0
DEFAULT_STATIC_ANALYSIS_OUTPUT_LIMIT_BYTES = 256 * 1024
DEFAULT_PERSISTENCE_ENABLED = False
DEFAULT_EVALUATION_MODE = "sync"
DEFAULT_WORKER_CONCURRENCY = 1
DEFAULT_WORKER_LEASE_SECONDS = 60.0
DEFAULT_WORKER_MAX_ATTEMPTS = 3
DEFAULT_OUTBOX_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_RETRY_BASE_DELAY_SECONDS = 5.0
DEFAULT_LLM_ENABLED = False
DEFAULT_LLM_PROVIDER_ID = "default-openai-compatible"
DEFAULT_LLM_TIMEOUT_SECONDS = 30.0
DEFAULT_LLM_MAX_ATTEMPTS = 2
DEFAULT_LLM_MAX_OUTPUT_TOKENS = 2000
DEFAULT_LLM_MAX_INPUT_BYTES = 100_000
DEFAULT_LLM_MAX_RESPONSE_BYTES = 256 * 1024
DEFAULT_LLM_MAX_ADVERSARIAL_TESTS = 5
DEFAULT_LLM_TEMPERATURE = 0.0
DEFAULT_LLM_TOP_P = 1.0
DEFAULT_LLM_DISAGREEMENT_THRESHOLD = 20.0
DEFAULT_BENCHMARK_ENABLED = False
DEFAULT_BENCHMARK_PROVIDER_ID = "default-benchmark-openai-compatible"
DEFAULT_BENCHMARK_GENERATION_CONCURRENCY = 2
DEFAULT_MAX_BENCHMARK_MODELS = 20
DEFAULT_MAX_BENCHMARK_TASKS = 20
DEFAULT_MAX_BENCHMARK_SAMPLES_PER_TASK = 10
DEFAULT_MAX_BENCHMARK_TOTAL_GENERATIONS = 500


class ExecutionBackend(StrEnum):
    DOCKER = "docker"
    LOCAL = "local"


class EvaluationMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"


def _environment_value(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    if not value:
        raise ValueError(f"{name} must not be blank")
    return value


def _positive_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    value = default if raw_value is None else float(raw_value)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    value = default if raw_value is None else int(raw_value)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw_value = os.getenv(name)
    value = default if raw_value is None else float(raw_value)
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings with safe local-development defaults."""

    app_name: str = DEFAULT_APP_NAME
    app_env: str = DEFAULT_APP_ENV
    log_level: str = DEFAULT_LOG_LEVEL
    default_execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT
    max_code_size: int = DEFAULT_MAX_CODE_SIZE
    execution_backend: ExecutionBackend = ExecutionBackend.DOCKER
    sandbox_image: str = DEFAULT_SANDBOX_IMAGE
    sandbox_memory_mb: int = DEFAULT_SANDBOX_MEMORY_MB
    sandbox_cpus: float = DEFAULT_SANDBOX_CPUS
    sandbox_pids_limit: int = DEFAULT_SANDBOX_PIDS_LIMIT
    sandbox_timeout_seconds: float = DEFAULT_SANDBOX_TIMEOUT_SECONDS
    sandbox_output_limit_bytes: int = DEFAULT_SANDBOX_OUTPUT_LIMIT_BYTES
    static_analysis_enabled: bool = DEFAULT_STATIC_ANALYSIS_ENABLED
    static_analysis_timeout_seconds: float = DEFAULT_STATIC_ANALYSIS_TIMEOUT_SECONDS
    static_analysis_output_limit_bytes: int = DEFAULT_STATIC_ANALYSIS_OUTPUT_LIMIT_BYTES
    persistence_enabled: bool = DEFAULT_PERSISTENCE_ENABLED
    database_url: str | None = None
    evaluation_mode: EvaluationMode = EvaluationMode.SYNC
    redis_url: str | None = None
    worker_concurrency: int = DEFAULT_WORKER_CONCURRENCY
    worker_lease_seconds: float = DEFAULT_WORKER_LEASE_SECONDS
    worker_max_attempts: int = DEFAULT_WORKER_MAX_ATTEMPTS
    outbox_poll_interval_seconds: float = DEFAULT_OUTBOX_POLL_INTERVAL_SECONDS
    retry_base_delay_seconds: float = DEFAULT_RETRY_BASE_DELAY_SECONDS
    llm_enabled: bool = DEFAULT_LLM_ENABLED
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_provider_id: str = DEFAULT_LLM_PROVIDER_ID
    llm_judge_models: tuple[str, ...] = ()
    llm_adversarial_model: str | None = None
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    llm_max_attempts: int = DEFAULT_LLM_MAX_ATTEMPTS
    llm_max_output_tokens: int = DEFAULT_LLM_MAX_OUTPUT_TOKENS
    llm_max_input_bytes: int = DEFAULT_LLM_MAX_INPUT_BYTES
    llm_max_response_bytes: int = DEFAULT_LLM_MAX_RESPONSE_BYTES
    llm_max_adversarial_tests: int = DEFAULT_LLM_MAX_ADVERSARIAL_TESTS
    llm_temperature: float = DEFAULT_LLM_TEMPERATURE
    llm_top_p: float = DEFAULT_LLM_TOP_P
    llm_disagreement_threshold: float = DEFAULT_LLM_DISAGREEMENT_THRESHOLD
    benchmark_enabled: bool = DEFAULT_BENCHMARK_ENABLED
    benchmark_config_path: str | None = None
    benchmark_base_url: str | None = None
    benchmark_api_key: str | None = None
    benchmark_provider_id: str = DEFAULT_BENCHMARK_PROVIDER_ID
    benchmark_generation_concurrency: int = DEFAULT_BENCHMARK_GENERATION_CONCURRENCY
    max_benchmark_models: int = DEFAULT_MAX_BENCHMARK_MODELS
    max_benchmark_tasks: int = DEFAULT_MAX_BENCHMARK_TASKS
    max_benchmark_samples_per_task: int = DEFAULT_MAX_BENCHMARK_SAMPLES_PER_TASK
    max_benchmark_total_generations: int = DEFAULT_MAX_BENCHMARK_TOTAL_GENERATIONS

    def __post_init__(self) -> None:
        if self.persistence_enabled and not self.database_url:
            raise ValueError("DATABASE_URL is required when persistence is enabled")
        if self.database_url and not self.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use postgresql+asyncpg")
        if self.evaluation_mode is EvaluationMode.ASYNC:
            if not self.persistence_enabled:
                raise ValueError("PERSISTENCE_ENABLED must be true in async evaluation mode")
            if not self.redis_url:
                raise ValueError("REDIS_URL is required in async evaluation mode")
        if self.redis_url and not self.redis_url.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must use redis or rediss")
        if self.worker_concurrency <= 0:
            raise ValueError("WORKER_CONCURRENCY must be greater than zero")
        if self.worker_lease_seconds <= 0:
            raise ValueError("WORKER_LEASE_SECONDS must be greater than zero")
        if self.worker_max_attempts <= 0:
            raise ValueError("WORKER_MAX_ATTEMPTS must be greater than zero")
        if self.outbox_poll_interval_seconds <= 0:
            raise ValueError("OUTBOX_POLL_INTERVAL_SECONDS must be greater than zero")
        if self.retry_base_delay_seconds <= 0:
            raise ValueError("RETRY_BASE_DELAY_SECONDS must be greater than zero")
        if self.llm_enabled:
            if not self.persistence_enabled:
                raise ValueError("PERSISTENCE_ENABLED must be true when LLM evaluation is enabled")
            if not self.llm_base_url:
                raise ValueError("LLM_BASE_URL is required when LLM evaluation is enabled")
            if not self.llm_api_key:
                raise ValueError("LLM_API_KEY is required when LLM evaluation is enabled")
            if not self.llm_judge_models:
                raise ValueError("LLM_JUDGE_MODEL or LLM_JUDGE_MODELS is required")
            if not self.llm_adversarial_model:
                raise ValueError("LLM_ADVERSARIAL_MODEL is required")
        if self.llm_base_url and not self.llm_base_url.startswith(("http://", "https://")):
            raise ValueError("LLM_BASE_URL must use http or https")
        if not self.llm_provider_id.strip():
            raise ValueError("LLM_PROVIDER_ID must not be blank")
        if any(not model.strip() for model in self.llm_judge_models):
            raise ValueError("LLM judge model names must not be blank")
        if self.llm_timeout_seconds <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS must be greater than zero")
        if self.llm_max_attempts <= 0:
            raise ValueError("LLM_MAX_ATTEMPTS must be greater than zero")
        if self.llm_max_output_tokens <= 0:
            raise ValueError("LLM_MAX_OUTPUT_TOKENS must be greater than zero")
        if self.llm_max_input_bytes <= 0 or self.llm_max_response_bytes <= 0:
            raise ValueError("LLM input and response limits must be greater than zero")
        if self.llm_max_adversarial_tests <= 0:
            raise ValueError("LLM_MAX_ADVERSARIAL_TESTS must be greater than zero")
        if not 0 <= self.llm_temperature <= 2:
            raise ValueError("LLM_TEMPERATURE must be between 0 and 2")
        if not 0 < self.llm_top_p <= 1:
            raise ValueError("LLM_TOP_P must be greater than zero and at most one")
        if not 0 <= self.llm_disagreement_threshold <= 100:
            raise ValueError("LLM_DISAGREEMENT_THRESHOLD must be between 0 and 100")
        if self.benchmark_enabled:
            if not self.persistence_enabled or not self.database_url:
                raise ValueError("Benchmarking requires PostgreSQL persistence")
            if not self.redis_url:
                raise ValueError("REDIS_URL is required when benchmarking is enabled")
            if not self.benchmark_config_path and (
                not self.benchmark_base_url or not self.benchmark_api_key
            ):
                raise ValueError(
                    "BENCHMARK_CONFIG or BENCHMARK_BASE_URL and BENCHMARK_API_KEY are required "
                    "when benchmarking is enabled"
                )
        if self.benchmark_base_url and not self.benchmark_base_url.startswith(
            ("http://", "https://")
        ):
            raise ValueError("BENCHMARK_BASE_URL must use http or https")
        if not self.benchmark_provider_id.strip():
            raise ValueError("BENCHMARK_PROVIDER_ID must not be blank")
        benchmark_limits = (
            self.benchmark_generation_concurrency,
            self.max_benchmark_models,
            self.max_benchmark_tasks,
            self.max_benchmark_samples_per_task,
            self.max_benchmark_total_generations,
        )
        if any(value <= 0 for value in benchmark_limits):
            raise ValueError("Benchmark concurrency and limits must be greater than zero")

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from process environment variables."""
        persistence_enabled = _boolean("PERSISTENCE_ENABLED", DEFAULT_PERSISTENCE_ENABLED)
        database_url = os.getenv("DATABASE_URL")
        if database_url is not None:
            database_url = database_url.strip() or None
        redis_url = os.getenv("REDIS_URL")
        if redis_url is not None:
            redis_url = redis_url.strip() or None
        llm_base_url = os.getenv("LLM_BASE_URL")
        if llm_base_url is not None:
            llm_base_url = llm_base_url.strip().rstrip("/") or None
        llm_api_key = os.getenv("LLM_API_KEY")
        if llm_api_key is not None:
            llm_api_key = llm_api_key.strip() or None
        benchmark_base_url = os.getenv("BENCHMARK_BASE_URL")
        if benchmark_base_url is not None:
            benchmark_base_url = benchmark_base_url.strip().rstrip("/") or None
        benchmark_api_key = os.getenv("BENCHMARK_API_KEY")
        if benchmark_api_key is not None:
            benchmark_api_key = benchmark_api_key.strip() or None
        benchmark_config_path = os.getenv("BENCHMARK_CONFIG")
        if benchmark_config_path is not None:
            benchmark_config_path = benchmark_config_path.strip() or None
        judge_models_raw = os.getenv("LLM_JUDGE_MODELS", os.getenv("LLM_JUDGE_MODEL", ""))
        judge_models = tuple(
            model.strip() for model in judge_models_raw.split(",") if model.strip()
        )
        return cls(
            app_name=os.getenv("APP_NAME", DEFAULT_APP_NAME),
            app_env=os.getenv("APP_ENV", DEFAULT_APP_ENV),
            log_level=os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).upper(),
            default_execution_timeout=_positive_float(
                "DEFAULT_EXECUTION_TIMEOUT", DEFAULT_EXECUTION_TIMEOUT
            ),
            max_code_size=_positive_int("MAX_CODE_SIZE", DEFAULT_MAX_CODE_SIZE),
            execution_backend=ExecutionBackend(
                _environment_value("EXECUTION_BACKEND", DEFAULT_EXECUTION_BACKEND).lower()
            ),
            sandbox_image=_environment_value("SANDBOX_IMAGE", DEFAULT_SANDBOX_IMAGE),
            sandbox_memory_mb=_positive_int("SANDBOX_MEMORY_MB", DEFAULT_SANDBOX_MEMORY_MB),
            sandbox_cpus=_positive_float("SANDBOX_CPUS", DEFAULT_SANDBOX_CPUS),
            sandbox_pids_limit=_positive_int("SANDBOX_PIDS_LIMIT", DEFAULT_SANDBOX_PIDS_LIMIT),
            sandbox_timeout_seconds=_positive_float(
                "SANDBOX_TIMEOUT_SECONDS", DEFAULT_SANDBOX_TIMEOUT_SECONDS
            ),
            sandbox_output_limit_bytes=_positive_int(
                "SANDBOX_OUTPUT_LIMIT_BYTES", DEFAULT_SANDBOX_OUTPUT_LIMIT_BYTES
            ),
            static_analysis_enabled=_boolean(
                "STATIC_ANALYSIS_ENABLED", DEFAULT_STATIC_ANALYSIS_ENABLED
            ),
            static_analysis_timeout_seconds=_positive_float(
                "STATIC_ANALYSIS_TIMEOUT_SECONDS", DEFAULT_STATIC_ANALYSIS_TIMEOUT_SECONDS
            ),
            static_analysis_output_limit_bytes=_positive_int(
                "STATIC_ANALYSIS_OUTPUT_LIMIT_BYTES",
                DEFAULT_STATIC_ANALYSIS_OUTPUT_LIMIT_BYTES,
            ),
            persistence_enabled=persistence_enabled,
            database_url=database_url,
            evaluation_mode=EvaluationMode(
                _environment_value("EVALUATION_MODE", DEFAULT_EVALUATION_MODE).lower()
            ),
            redis_url=redis_url,
            worker_concurrency=_positive_int("WORKER_CONCURRENCY", DEFAULT_WORKER_CONCURRENCY),
            worker_lease_seconds=_positive_float(
                "WORKER_LEASE_SECONDS", DEFAULT_WORKER_LEASE_SECONDS
            ),
            worker_max_attempts=_positive_int("WORKER_MAX_ATTEMPTS", DEFAULT_WORKER_MAX_ATTEMPTS),
            outbox_poll_interval_seconds=_positive_float(
                "OUTBOX_POLL_INTERVAL_SECONDS", DEFAULT_OUTBOX_POLL_INTERVAL_SECONDS
            ),
            retry_base_delay_seconds=_positive_float(
                "RETRY_BASE_DELAY_SECONDS", DEFAULT_RETRY_BASE_DELAY_SECONDS
            ),
            llm_enabled=_boolean("LLM_ENABLED", DEFAULT_LLM_ENABLED),
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_provider_id=_environment_value("LLM_PROVIDER_ID", DEFAULT_LLM_PROVIDER_ID),
            llm_judge_models=judge_models,
            llm_adversarial_model=(os.getenv("LLM_ADVERSARIAL_MODEL", "").strip() or None),
            llm_timeout_seconds=_positive_float("LLM_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS),
            llm_max_attempts=_positive_int("LLM_MAX_ATTEMPTS", DEFAULT_LLM_MAX_ATTEMPTS),
            llm_max_output_tokens=_positive_int(
                "LLM_MAX_OUTPUT_TOKENS", DEFAULT_LLM_MAX_OUTPUT_TOKENS
            ),
            llm_max_input_bytes=_positive_int("LLM_MAX_INPUT_BYTES", DEFAULT_LLM_MAX_INPUT_BYTES),
            llm_max_response_bytes=_positive_int(
                "LLM_MAX_RESPONSE_BYTES", DEFAULT_LLM_MAX_RESPONSE_BYTES
            ),
            llm_max_adversarial_tests=_positive_int(
                "LLM_MAX_ADVERSARIAL_TESTS", DEFAULT_LLM_MAX_ADVERSARIAL_TESTS
            ),
            llm_temperature=_bounded_float("LLM_TEMPERATURE", DEFAULT_LLM_TEMPERATURE, 0, 2),
            llm_top_p=_bounded_float("LLM_TOP_P", DEFAULT_LLM_TOP_P, 0.000001, 1),
            llm_disagreement_threshold=_bounded_float(
                "LLM_DISAGREEMENT_THRESHOLD",
                DEFAULT_LLM_DISAGREEMENT_THRESHOLD,
                0,
                100,
            ),
            benchmark_enabled=_boolean("BENCHMARK_ENABLED", DEFAULT_BENCHMARK_ENABLED),
            benchmark_config_path=benchmark_config_path,
            benchmark_base_url=benchmark_base_url,
            benchmark_api_key=benchmark_api_key,
            benchmark_provider_id=_environment_value(
                "BENCHMARK_PROVIDER_ID", DEFAULT_BENCHMARK_PROVIDER_ID
            ),
            benchmark_generation_concurrency=_positive_int(
                "BENCHMARK_GENERATION_CONCURRENCY",
                DEFAULT_BENCHMARK_GENERATION_CONCURRENCY,
            ),
            max_benchmark_models=_positive_int(
                "MAX_BENCHMARK_MODELS", DEFAULT_MAX_BENCHMARK_MODELS
            ),
            max_benchmark_tasks=_positive_int("MAX_BENCHMARK_TASKS", DEFAULT_MAX_BENCHMARK_TASKS),
            max_benchmark_samples_per_task=_positive_int(
                "MAX_BENCHMARK_SAMPLES_PER_TASK",
                DEFAULT_MAX_BENCHMARK_SAMPLES_PER_TASK,
            ),
            max_benchmark_total_generations=_positive_int(
                "MAX_BENCHMARK_TOTAL_GENERATIONS",
                DEFAULT_MAX_BENCHMARK_TOTAL_GENERATIONS,
            ),
        )
