"""Stable, secret-free generation failure normalization for benchmark reporting."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

GenerationFailureCategory = Literal[
    "rate_limited",
    "unauthorized",
    "forbidden",
    "not_found",
    "provider_unavailable",
    "provider_timeout",
    "provider_error",
    "invalid_response",
    "malformed_output",
    "unknown",
]

GENERATION_FAILURE_CATEGORY_ORDER: tuple[GenerationFailureCategory, ...] = (
    "rate_limited",
    "unauthorized",
    "forbidden",
    "not_found",
    "provider_unavailable",
    "provider_timeout",
    "provider_error",
    "invalid_response",
    "malformed_output",
    "unknown",
)

_NORMALIZED_FAILURE_CODES: dict[str, GenerationFailureCategory] = {
    "provider_rate_limited": "rate_limited",
    "provider_unauthorized": "unauthorized",
    "provider_forbidden": "forbidden",
    "provider_not_found": "not_found",
    "provider_unavailable": "provider_unavailable",
    "provider_timeout": "provider_timeout",
    "provider_error": "provider_error",
    "provider_request_rejected": "provider_error",
    "provider_refusal": "provider_error",
    "provider_not_configured": "provider_error",
    "malformed_provider_response": "invalid_response",
    "provider_output_too_large": "invalid_response",
    "output_too_large": "invalid_response",
    "malformed_output": "malformed_output",
    "empty_output": "malformed_output",
}


def normalize_generation_failure(code: str | None) -> GenerationFailureCategory:
    """Normalize a persisted sanitized code without inspecting raw provider messages."""
    if code is None:
        return "unknown"
    return _NORMALIZED_FAILURE_CODES.get(code, "unknown")


def generation_failure_category_counts(codes: Iterable[str | None]) -> dict[str, int]:
    """Count normalized categories in a stable documented presentation order."""
    counts = {category: 0 for category in GENERATION_FAILURE_CATEGORY_ORDER}
    for code in codes:
        counts[normalize_generation_failure(code)] += 1
    return {category: count for category, count in counts.items() if count}
