from __future__ import annotations

from app.benchmarks.reliability import (
    GENERATION_FAILURE_CATEGORY_ORDER,
    UNKNOWN_FAILURE_DETAIL,
    decode_failure_diagnostic,
    encode_failure_diagnostic,
    generation_failure_category_counts,
    generation_failure_detail_counts,
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


def test_sanitized_failure_details_round_trip_without_changing_public_taxonomy() -> None:
    persisted = [
        encode_failure_diagnostic("malformed_provider_response", "missing_choices"),
        encode_failure_diagnostic("malformed_provider_response", "null_content"),
        encode_failure_diagnostic("malformed_provider_response", "reasoning_only"),
        encode_failure_diagnostic("malformed_provider_response", "unsupported_content_type"),
        "malformed_provider_response",
        encode_failure_diagnostic("provider_refusal", "refusal"),
        encode_failure_diagnostic("empty_output", "empty_output"),
    ]

    assert [normalize_generation_failure(value) for value in persisted] == [
        "invalid_response",
        "invalid_response",
        "invalid_response",
        "invalid_response",
        "invalid_response",
        "provider_error",
        "malformed_output",
    ]
    assert generation_failure_detail_counts(persisted) == {
        "provider_error": {"refusal": 1},
        "invalid_response": {
            "missing_choices": 1,
            "null_content": 1,
            "reasoning_only": 1,
            "unsupported_content_type": 1,
            UNKNOWN_FAILURE_DETAIL: 1,
        },
        "malformed_output": {"empty_output": 1},
    }


def test_failure_detail_encoding_is_allowlisted_bounded_and_secret_free() -> None:
    unsafe_values = [
        "raw assistant content",
        "hidden reasoning",
        '{"tool":{"arguments":"secret"}}',
        "system prompt",
        "Authorization: Bearer secret-key",
    ]

    for unsafe in unsafe_values:
        persisted = encode_failure_diagnostic("malformed_provider_response", unsafe)
        assert persisted == "malformed_provider_response"
        assert unsafe not in persisted
        assert decode_failure_diagnostic(persisted).detail_code is None
        assert len(persisted) <= 128


def test_worker_preserves_sanitized_typed_codes_and_generic_errors_are_not_unavailable() -> None:
    assert _generation_failure_code("provider_unauthorized") == "provider_unauthorized"
    assert _generation_failure_code("provider_forbidden") == "provider_forbidden"
    assert _generation_failure_code("provider_not_found") == "provider_not_found"
    assert _generation_failure_code("unexpected_provider_failure") == "provider_error"
