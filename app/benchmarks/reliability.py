"""Stable, secret-free generation failure normalization for benchmark reporting."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
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

UNKNOWN_FAILURE_DETAIL = "unknown_detail"
_FAILURE_DETAIL_SEPARATOR = "::"
_MAX_PERSISTED_FAILURE_LENGTH = 128
_SAFE_PROVIDER_FAILURE_DETAILS = frozenset(
    {
        "empty_choices",
        "empty_content",
        "invalid_choice",
        "invalid_choices_type",
        "invalid_message_type",
        "malformed_json",
        "missing_choices",
        "missing_content",
        "missing_message",
        "null_content",
        "reasoning_only",
        "refusal",
        "tool_call_only",
        "unsupported_content_type",
        "unsupported_root_type",
    }
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


@dataclass(frozen=True, slots=True)
class FailureDiagnostic:
    code: str | None
    detail_code: str | None


def encode_failure_diagnostic(code: str, detail_code: str | None) -> str:
    """Encode only allowlisted detail tokens within the existing bounded database column."""
    if detail_code not in _SAFE_PROVIDER_FAILURE_DETAILS:
        return code
    encoded = f"{code}{_FAILURE_DETAIL_SEPARATOR}{detail_code}"
    return encoded if len(encoded) <= _MAX_PERSISTED_FAILURE_LENGTH else code


def decode_failure_diagnostic(value: str | None) -> FailureDiagnostic:
    """Decode a current composite or an unchanged historical failure code."""
    if value is None:
        return FailureDiagnostic(code=None, detail_code=None)
    code, separator, detail_code = value.rpartition(_FAILURE_DETAIL_SEPARATOR)
    if separator and code and detail_code in _SAFE_PROVIDER_FAILURE_DETAILS:
        return FailureDiagnostic(code=code, detail_code=detail_code)
    return FailureDiagnostic(code=value, detail_code=None)


def normalize_generation_failure(code: str | None) -> GenerationFailureCategory:
    """Normalize a persisted sanitized code without inspecting raw provider messages."""
    decoded = decode_failure_diagnostic(code)
    if decoded.code is None:
        return "unknown"
    return _NORMALIZED_FAILURE_CODES.get(decoded.code, "unknown")


def generation_failure_category_counts(codes: Iterable[str | None]) -> dict[str, int]:
    """Count normalized categories in a stable documented presentation order."""
    counts = {category: 0 for category in GENERATION_FAILURE_CATEGORY_ORDER}
    for code in codes:
        counts[normalize_generation_failure(code)] += 1
    return {category: count for category, count in counts.items() if count}


def generation_failure_detail_counts(
    codes: Iterable[str | None],
) -> dict[str, dict[str, int]]:
    """Group bounded details under the stable public taxonomy without fabricating history."""
    counts: dict[str, dict[str, int]] = {}
    for value in codes:
        category = normalize_generation_failure(value)
        detail = decode_failure_diagnostic(value).detail_code or UNKNOWN_FAILURE_DETAIL
        category_counts = counts.setdefault(category, {})
        category_counts[detail] = category_counts.get(detail, 0) + 1
    return {
        category: {
            detail: counts[category][detail]
            for detail in sorted(
                counts[category], key=lambda item: (item == UNKNOWN_FAILURE_DETAIL, item)
            )
        }
        for category in GENERATION_FAILURE_CATEGORY_ORDER
        if category in counts
    }
