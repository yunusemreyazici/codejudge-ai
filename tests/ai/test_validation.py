from __future__ import annotations

import pytest

from app.ai.models import GeneratedTestProposal
from app.ai.validation import deduplicate_and_validate, validate_test

pytestmark = pytest.mark.ai


def _proposal(code: str, name: str = "test_case") -> GeneratedTestProposal:
    return GeneratedTestProposal(name=name, rationale="Edge case", code=code)


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("def test_case(:\n", "invalid_python"),
        ("import socket\ndef test_case(): pass\n", "prohibited_import"),
        ("pytest_plugins = ['x']\ndef test_case(): pass\n", "pytest_plugin_declaration"),
        ("def helper(): pass\n", "missing_named_test"),
    ],
)
def test_structural_rejections(code: str, reason: str) -> None:
    result = validate_test(_proposal(code))
    assert not result.accepted
    assert result.reason == reason


def test_valid_test_is_accepted() -> None:
    assert validate_test(_proposal("def test_case():\n    assert True\n")).accepted


def test_duplicate_name_or_source_is_rejected() -> None:
    source = "def test_case():\n    assert True\n"
    results = deduplicate_and_validate([_proposal(source), _proposal(source)], 5)
    assert results[0][1].accepted
    assert results[1][1].reason == "duplicate_test"


def test_excess_test_count_is_rejected_without_truncation() -> None:
    proposals = [_proposal(f"def test_case():\n    assert {index} >= 0\n") for index in range(2)]
    with pytest.raises(ValueError, match="too_many_generated_tests"):
        deduplicate_and_validate(proposals, 1)


def test_oversized_individual_test_is_rejected() -> None:
    proposal = _proposal("def test_case():\n    value = '" + ("x" * 17_000) + "'\n")
    result = validate_test(proposal)
    assert not result.accepted
    assert result.reason == "test_too_large"
