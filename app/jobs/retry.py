"""Deterministic retry classification and backoff policy."""

from __future__ import annotations

from dataclasses import dataclass

from app.evaluator.engine import EvaluationInfrastructureError


@dataclass(frozen=True, slots=True)
class FailureDecision:
    retryable: bool
    category: str
    code: str


class EvaluationIntegrityError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def retry_delay_seconds(attempt: int, base_delay_seconds: float) -> float:
    if attempt <= 0:
        raise ValueError("attempt must be greater than zero")
    return float(base_delay_seconds * (3.0 ** (attempt - 1)))


def classify_failure(error: BaseException) -> FailureDecision:
    if isinstance(error, EvaluationIntegrityError):
        return FailureDecision(retryable=False, category="integrity", code=error.code)
    if isinstance(error, EvaluationInfrastructureError):
        message = str(error).lower()
        if "docker" in message or "sandbox" in message or "image" in message:
            code = "sandbox_unavailable"
        elif "analysis" in message or "analyzer" in message:
            code = "analysis_unavailable"
        elif "persistence" in message or "database" in message:
            code = "persistence_unavailable"
        else:
            code = "evaluation_infrastructure"
        return FailureDecision(retryable=True, category="infrastructure", code=code)
    return FailureDecision(retryable=False, category="worker", code="unexpected_error")
