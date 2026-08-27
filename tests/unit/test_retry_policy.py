import pytest

from app.evaluator.engine import EvaluationInfrastructureError
from app.jobs.retry import EvaluationIntegrityError, classify_failure, retry_delay_seconds


def test_retry_backoff_is_deterministic() -> None:
    assert [retry_delay_seconds(attempt, 5) for attempt in (1, 2, 3)] == [5, 15, 45]


def test_retry_backoff_rejects_invalid_attempt() -> None:
    with pytest.raises(ValueError):
        retry_delay_seconds(0, 5)


def test_known_infrastructure_failure_is_retryable() -> None:
    decision = classify_failure(EvaluationInfrastructureError("Docker daemon unavailable"))

    assert decision.retryable is True
    assert decision.category == "infrastructure"
    assert decision.code == "sandbox_unavailable"


def test_integrity_and_unexpected_failures_are_not_blindly_retried() -> None:
    integrity = classify_failure(EvaluationIntegrityError("source_identity_mismatch"))
    unexpected = classify_failure(RuntimeError("bug"))

    assert integrity.retryable is False
    assert integrity.category == "integrity"
    assert unexpected.retryable is False
    assert unexpected.code == "unexpected_error"
