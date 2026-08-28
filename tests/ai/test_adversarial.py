from __future__ import annotations

import pytest

from app.ai.adversarial import AdversarialService
from app.ai.models import AIComponentStatus
from app.ai.providers.base import ProviderError
from tests.ai.fakes import FakeProvider, FakeSandbox, generated_output

pytestmark = pytest.mark.ai


def _service(provider: FakeProvider, sandbox: FakeSandbox) -> AdversarialService:
    return AdversarialService(
        provider,
        sandbox,
        provider_id="fake-provider",
        model="generator-a",
        max_tests=5,
        max_output_tokens=2000,
        temperature=0,
        top_p=1,
    )


async def test_reference_valid_candidate_failure_is_recorded() -> None:
    provider = FakeProvider()
    provider.add("adversarial", "generator-a", [generated_output()])
    sandbox = FakeSandbox(reference_passes=True, candidate_passes=False)
    result = await _service(provider, sandbox).evaluate(
        payload={},
        task_id="lru-cache",
        timeout_seconds=5,
        candidate_source="candidate",
        reference_source='"""Trusted LRU cache oracle"""\nreference',
    )
    assert result.status is AIComponentStatus.COMPLETED
    assert result.reference_valid == 1
    assert result.candidate_failed == 1
    assert result.robustness_score == 0
    assert len(sandbox.calls) == 2


async def test_test_failing_reference_never_runs_against_candidate() -> None:
    provider = FakeProvider()
    provider.add("adversarial", "generator-a", [generated_output()])
    sandbox = FakeSandbox(reference_passes=False)
    result = await _service(provider, sandbox).evaluate(
        payload={},
        task_id="lru-cache",
        timeout_seconds=5,
        candidate_source="candidate",
        reference_source='"""Trusted LRU cache oracle"""\nreference',
    )
    assert result.status is AIComponentStatus.UNAVAILABLE
    assert result.robustness_score is None
    assert result.tests[0].rejection_reason == "reference_failed"
    assert len(sandbox.calls) == 1


async def test_invalid_generated_structure_is_retained_as_rejected_artifact() -> None:
    provider = FakeProvider()
    output = generated_output()
    tests = output["tests"]
    assert isinstance(tests, list) and isinstance(tests[0], dict)
    tests[0]["code"] = "import subprocess\ndef test_repeated_update(): pass\n"
    provider.add("adversarial", "generator-a", [output])
    sandbox = FakeSandbox()
    result = await _service(provider, sandbox).evaluate(
        payload={},
        task_id="lru-cache",
        timeout_seconds=5,
        candidate_source="candidate",
        reference_source='"""Trusted LRU cache oracle"""\nreference',
    )
    assert result.robustness_score is None
    assert result.tests[0].rejection_reason == "prohibited_import"
    assert sandbox.calls == []


async def test_malformed_generator_response_is_component_error() -> None:
    provider = FakeProvider()
    provider.add("adversarial", "generator-a", ["not-json"])
    with pytest.raises(ProviderError, match="malformed_output"):
        await _service(provider, FakeSandbox()).evaluate(
            payload={},
            task_id="lru-cache",
            timeout_seconds=5,
            candidate_source="candidate",
            reference_source="reference",
        )


async def test_reference_timeout_rejects_test_without_candidate_execution() -> None:
    provider = FakeProvider()
    provider.add("adversarial", "generator-a", [generated_output()])
    sandbox = FakeSandbox(reference_timed_out=True)
    result = await _service(provider, sandbox).evaluate(
        payload={},
        task_id="lru-cache",
        timeout_seconds=5,
        candidate_source="candidate",
        reference_source='"""Trusted LRU cache oracle"""\nreference',
    )
    assert result.robustness_score is None
    assert result.tests[0].rejection_reason == "reference_timeout"
    assert len(sandbox.calls) == 1
