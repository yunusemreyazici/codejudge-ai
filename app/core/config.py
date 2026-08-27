"""Typed application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_APP_NAME = "CodeJudge AI"
DEFAULT_APP_ENV = "development"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_EXECUTION_TIMEOUT = 5.0
DEFAULT_MAX_CODE_SIZE = 100 * 1024


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


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings with safe local-development defaults."""

    app_name: str = DEFAULT_APP_NAME
    app_env: str = DEFAULT_APP_ENV
    log_level: str = DEFAULT_LOG_LEVEL
    default_execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT
    max_code_size: int = DEFAULT_MAX_CODE_SIZE

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from process environment variables."""
        return cls(
            app_name=os.getenv("APP_NAME", DEFAULT_APP_NAME),
            app_env=os.getenv("APP_ENV", DEFAULT_APP_ENV),
            log_level=os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).upper(),
            default_execution_timeout=_positive_float(
                "DEFAULT_EXECUTION_TIMEOUT", DEFAULT_EXECUTION_TIMEOUT
            ),
            max_code_size=_positive_int("MAX_CODE_SIZE", DEFAULT_MAX_CODE_SIZE),
        )
