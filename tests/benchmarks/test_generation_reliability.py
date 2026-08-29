from __future__ import annotations

from app.benchmarks.reliability import (
    GENERATION_FAILURE_CATEGORY_ORDER,
    generation_failure_category_counts,
    normalize_generation_failure,
)
from app.benchmarks.worker import _generation_failure_code


def test_failure_taxonomy_normalizes_typed_provider_codes() -> None:
    assert normalize_generation_failure("provider_rate_limited") == "rate_limited"
    assert normalize_generation_failure("provider_unauthorized") == "unauthorized"
    assert normalize_generation_failure("provider_forbidden") == "forbidden"
    assert normalize_generation_failure("provider_not_found") == "not_found"
    assert normalize_generation_failure("provider_unavailable") == "provider_unavailable"
    assert normalize_generation_failure("provider_timeout") == "provider_timeout"
    assert normalize_generation_failure("provider_error") == "provider_error"
    assert normalize_generation_failure("provider_request_rejected") == "provider_error"
    assert normalize_generation_failure("malformed_provider_response") == "invalid_response"
    assert normalize_generation_failure("malformed_output") == "malformed_output"
    assert normalize_generation_failure("historical_unclassified_code") == "unknown"


def test_failure_category_counts_have_deterministic_documented_order() -> None:
    counts = generation_failure_category_counts(
        [
            "historical_unclassified_code",
            "malformed_output",
            "provider_timeout",
            "provider_rate_limited",
            "provider_timeout",
        ]
    )

    assert list(counts) == [
        category for category in GENERATION_FAILURE_CATEGORY_ORDER if category in counts
    ]
    assert counts == {
        "rate_limited": 1,
        "provider_timeout": 2,
        "malformed_output": 1,
        "unknown": 1,
    }


def test_worker_preserves_sanitized_typed_codes_and_generic_errors_are_not_unavailable() -> None:
    assert _generation_failure_code("provider_unauthorized") == "provider_unauthorized"
    assert _generation_failure_code("provider_forbidden") == "provider_forbidden"
    assert _generation_failure_code("provider_not_found") == "provider_not_found"
    assert _generation_failure_code("unexpected_provider_failure") == "provider_error"
