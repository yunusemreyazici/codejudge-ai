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


class ExecutionBackend(StrEnum):
    DOCKER = "docker"
    LOCAL = "local"


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

    def __post_init__(self) -> None:
        if self.persistence_enabled and not self.database_url:
            raise ValueError("DATABASE_URL is required when persistence is enabled")
        if self.database_url and not self.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use postgresql+asyncpg")

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from process environment variables."""
        persistence_enabled = _boolean("PERSISTENCE_ENABLED", DEFAULT_PERSISTENCE_ENABLED)
        database_url = os.getenv("DATABASE_URL")
        if database_url is not None:
            database_url = database_url.strip() or None
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
        )
