"""Bounded generation, structural validation, oracle checking, and candidate execution."""

from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from app.ai.models import (
    AdversarialResult,
    AdversarialTestArtifact,
    AIComponentStatus,
    GeneratedTestsOutput,
    StructuredLLMRequest,
)
from app.ai.prompts import (
    ADVERSARIAL_PROMPT_HASH,
    ADVERSARIAL_PROMPT_VERSION,
    ADVERSARIAL_SYSTEM_PROMPT,
    rendered_input_hash,
)
from app.ai.providers.base import LLMProvider, ProviderError
from app.ai.sandbox import AdversarialSandbox
from app.ai.validation import deduplicate_and_validate


class AdversarialService:
    def __init__(
        self,
        provider: LLMProvider,
        sandbox: AdversarialSandbox,
        *,
        provider_id: str,
        model: str,
        max_tests: int,
        max_output_tokens: int,
        temperature: float,
        top_p: float,
    ) -> None:
        self._provider = provider
        self._sandbox = sandbox
        self._provider_id = provider_id
        self._model = model
        self._max_tests = max_tests
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._top_p = top_p

    async def evaluate(
        self,
        *,
        payload: dict[str, object],
        task_id: str,
        timeout_seconds: float,
        candidate_source: str,
        reference_source: str,
    ) -> AdversarialResult:
        response = await self._provider.complete_structured(
            StructuredLLMRequest(
                component="adversarial",
                model=self._model,
                system_prompt=ADVERSARIAL_SYSTEM_PROMPT,
                input_payload=payload,
                response_schema=GeneratedTestsOutput.model_json_schema(),
                max_output_tokens=self._max_output_tokens,
                temperature=self._temperature,
                top_p=self._top_p,
            )
        )
        try:
            output = GeneratedTestsOutput.model_validate(json.loads(response.content))
            validated = deduplicate_and_validate(output.tests, self._max_tests)
        except (json.JSONDecodeError, ValidationError) as error:
            raise ProviderError("malformed_output") from error
        except ValueError as error:
            raise ProviderError(str(error)) from error

        artifacts: list[AdversarialTestArtifact] = []
        structurally_accepted = 0
        reference_valid = 0
        candidate_passed = 0
        for proposal, structural in validated:
            source_hash = hashlib.sha256(proposal.code.encode("utf-8")).hexdigest()
            if not structural.accepted:
                artifacts.append(
                    AdversarialTestArtifact(
                        name=proposal.name,
                        rationale=proposal.rationale,
                        code=proposal.code,
                        source_hash=source_hash,
                        structurally_valid=False,
                        reference_valid=False,
                        rejection_reason=structural.reason,
                    )
                )
                continue
            structurally_accepted += 1
            reference = await self._sandbox.run(
                solution_source=reference_source,
                test_source=proposal.code,
                task_id=task_id,
                timeout_seconds=timeout_seconds,
            )
            reference_ok = _successful_test_run(reference)
            if not reference_ok:
                artifacts.append(
                    AdversarialTestArtifact(
                        name=proposal.name,
                        rationale=proposal.rationale,
                        code=proposal.code,
                        source_hash=source_hash,
                        structurally_valid=True,
                        reference_valid=False,
                        rejection_reason=_run_rejection_reason(reference, "reference"),
                    )
                )
                continue
            reference_valid += 1
            candidate = await self._sandbox.run(
                solution_source=candidate_source,
                test_source=proposal.code,
                task_id=task_id,
                timeout_seconds=timeout_seconds,
            )
            if candidate.infrastructure_error is not None:
                return AdversarialResult(
                    status=AIComponentStatus.UNAVAILABLE,
                    reason="adversarial_sandbox_unavailable",
                    generated=len(output.tests),
                    structurally_accepted=structurally_accepted,
                    reference_valid=reference_valid,
                    candidate_passed=candidate_passed,
                    candidate_failed=max(0, reference_valid - candidate_passed - 1),
                    tests=artifacts,
                    provider_id=self._provider_id,
                    model=self._model,
                    prompt_version=ADVERSARIAL_PROMPT_VERSION,
                    prompt_hash=ADVERSARIAL_PROMPT_HASH,
                    rendered_input_hash=rendered_input_hash(payload),
                    provider_response_id=response.response_id,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    latency_ms=response.latency_ms,
                    raw_response_hash=hashlib.sha256(response.content.encode("utf-8")).hexdigest(),
                )
            candidate_ok = _successful_test_run(candidate)
            candidate_passed += int(candidate_ok)
            artifacts.append(
                AdversarialTestArtifact(
                    name=proposal.name,
                    rationale=proposal.rationale,
                    code=proposal.code,
                    source_hash=source_hash,
                    structurally_valid=True,
                    reference_valid=True,
                    candidate_passed=candidate_ok,
                    rejection_reason=(
                        None if candidate_ok else _run_rejection_reason(candidate, "candidate")
                    ),
                )
            )
        robustness = (
            None if reference_valid == 0 else round(candidate_passed / reference_valid * 100, 2)
        )
        return AdversarialResult(
            status=(
                AIComponentStatus.COMPLETED
                if robustness is not None
                else AIComponentStatus.UNAVAILABLE
            ),
            reason=None if robustness is not None else "no_valid_adversarial_tests",
            generated=len(output.tests),
            structurally_accepted=structurally_accepted,
            reference_valid=reference_valid,
            candidate_passed=candidate_passed,
            candidate_failed=reference_valid - candidate_passed,
            robustness_score=robustness,
            tests=artifacts,
            provider_id=self._provider_id,
            model=self._model,
            prompt_version=ADVERSARIAL_PROMPT_VERSION,
            prompt_hash=ADVERSARIAL_PROMPT_HASH,
            rendered_input_hash=rendered_input_hash(payload),
            provider_response_id=response.response_id,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=response.latency_ms,
            raw_response_hash=hashlib.sha256(response.content.encode("utf-8")).hexdigest(),
        )


def _successful_test_run(result: object) -> bool:
    from app.evaluator.models import RunnerResult

    if not isinstance(result, RunnerResult):
        return False
    return (
        result.infrastructure_error is None
        and result.sandbox_error is None
        and not result.timed_out
        and not result.oom_killed
        and not result.syntax_error
        and not result.import_error
        and result.total > 0
        and result.failed == 0
        and result.passed == result.total
    )


def _run_rejection_reason(result: object, prefix: str) -> str:
    from app.evaluator.models import RunnerResult

    if not isinstance(result, RunnerResult):
        return f"{prefix}_invalid_result"
    if result.infrastructure_error is not None:
        return f"{prefix}_infrastructure_unavailable"
    if result.timed_out:
        return f"{prefix}_timeout"
    if result.oom_killed:
        return f"{prefix}_oom"
    if result.syntax_error or result.import_error or result.sandbox_error is not None:
        return f"{prefix}_invalid_test"
    return f"{prefix}_failed"
