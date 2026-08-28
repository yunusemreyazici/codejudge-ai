"""Bounded AI orchestration that cannot mutate deterministic evaluation evidence."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from app.ai.adversarial import AdversarialService
from app.ai.judge import JudgeService
from app.ai.models import (
    AdversarialResult,
    AIAssessment,
    AIComponentStatus,
    AIIdentity,
    AIProvenance,
    AIStatus,
    JudgeResult,
)
from app.ai.prompts import (
    ADVERSARIAL_PROMPT_HASH,
    ADVERSARIAL_PROMPT_VERSION,
    AI_SCORING_POLICY_VERSION,
    JUDGE_PROMPT_HASH,
    JUDGE_PROMPT_VERSION,
    adversarial_payload,
    canonical_json,
    judge_payload,
)
from app.ai.providers.base import LLMProvider, ProviderError
from app.ai.scoring import aggregate_judge_scores, calculate_ai_score
from app.snapshots.models import EvaluationSnapshot
from app.tasks.registry import RegisteredTask


class AIService:
    def __init__(
        self,
        *,
        enabled: bool,
        provider: LLMProvider | None,
        provider_id: str | None,
        judge_models: tuple[str, ...],
        adversarial_model: str | None,
        judge_service: JudgeService | None,
        adversarial_service: AdversarialService | None,
        max_input_bytes: int,
        max_output_tokens: int,
        temperature: float,
        top_p: float,
        disagreement_threshold: float,
    ) -> None:
        self._enabled = enabled
        self._provider = provider
        self._provider_id = provider_id
        self._judge_models = judge_models
        self._adversarial_model = adversarial_model
        self._judge = judge_service
        self._adversarial = adversarial_service
        self._max_input_bytes = max_input_bytes
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._top_p = top_p
        self._disagreement_threshold = disagreement_threshold

    def identity(self, task: RegisteredTask) -> AIIdentity:
        reference = reference_fingerprint(task)
        return AIIdentity(
            enabled=self._enabled,
            policy_version=AI_SCORING_POLICY_VERSION,
            judge_prompt_version=JUDGE_PROMPT_VERSION,
            judge_prompt_hash=JUDGE_PROMPT_HASH,
            adversarial_prompt_version=ADVERSARIAL_PROMPT_VERSION,
            adversarial_prompt_hash=ADVERSARIAL_PROMPT_HASH,
            provider_id=self._provider_id if self._enabled else None,
            judge_models=self._judge_models if self._enabled else (),
            adversarial_model=self._adversarial_model if self._enabled else None,
            temperature=self._temperature,
            top_p=self._top_p,
            max_output_tokens=self._max_output_tokens,
            reference_fingerprint=reference,
        )

    async def assess(
        self,
        *,
        snapshot: EvaluationSnapshot,
        task: RegisteredTask,
        expected_identity: AIIdentity | None = None,
    ) -> AIAssessment:
        identity = self.identity(task)
        provenance = _provenance(identity)
        if not self._enabled:
            return self._assessment(
                status=AIStatus.DISABLED,
                reason=None,
                snapshot=snapshot,
                identity=identity,
                provenance=provenance,
            )
        if expected_identity is not None and expected_identity != identity:
            return self._assessment(
                status=AIStatus.SKIPPED,
                reason="ai_identity_mismatch",
                snapshot=snapshot,
                identity=expected_identity,
                provenance=_provenance(expected_identity),
            )
        if self._judge is None or self._provider is None:
            return self._assessment(
                status=AIStatus.UNAVAILABLE,
                reason="provider_unavailable",
                snapshot=snapshot,
                identity=identity,
                provenance=provenance,
            )

        task_payload = task.specification.model_dump(mode="json")
        evidence = _deterministic_evidence(snapshot)
        judge_input = judge_payload(
            task=task_payload,
            candidate_source=snapshot.source_text,
            deterministic_evidence=evidence,
        )
        adversarial_input = adversarial_payload(
            task=task_payload,
            candidate_source=snapshot.source_text,
            deterministic_summary=evidence,
        )
        if any(
            len(canonical_json(payload).encode("utf-8")) > self._max_input_bytes
            for payload in (judge_input, adversarial_input)
        ):
            return self._assessment(
                status=AIStatus.SKIPPED,
                reason="input_too_large",
                snapshot=snapshot,
                identity=identity,
                provenance=provenance,
            )

        judge_results, judge_errors = await self._run_judges(
            judge_input, len(snapshot.source_text.splitlines()) or 1
        )
        judge_score: float | None = None
        spread: float | None = None
        disputed = False
        if judge_results:
            judge_score, spread = aggregate_judge_scores([item.score for item in judge_results])
            disputed = spread > self._disagreement_threshold

        adversarial = await self._run_adversarial(
            task=task,
            payload=adversarial_input,
            candidate_source=snapshot.source_text,
        )
        adversarial_valid = (
            adversarial is not None
            and adversarial.status is AIComponentStatus.COMPLETED
            and adversarial.robustness_score is not None
        )
        all_judges_valid = len(judge_results) == len(self._judge_models) and not judge_errors
        if disputed:
            status = AIStatus.DISPUTED
            reason = "judge_disagreement"
        elif all_judges_valid and adversarial_valid:
            status = AIStatus.COMPLETED
            reason = None
        elif judge_results or adversarial_valid:
            status = AIStatus.PARTIAL
            reason = judge_errors[0] if judge_errors else _adversarial_reason(adversarial)
        else:
            status = AIStatus.UNAVAILABLE
            reason = judge_errors[0] if judge_errors else _adversarial_reason(adversarial)

        ai_score = None
        if (
            status is AIStatus.COMPLETED
            and judge_score is not None
            and adversarial is not None
            and adversarial.robustness_score is not None
        ):
            ai_score = calculate_ai_score(judge_score, adversarial.robustness_score)
        return self._assessment(
            status=status,
            reason=reason,
            snapshot=snapshot,
            identity=identity,
            provenance=provenance,
            ai_score=ai_score,
            judge_score=judge_score,
            judge_disputed=disputed,
            judge_disagreement_spread=spread,
            judge_results=judge_results,
            adversarial_tests=adversarial,
        )

    async def _run_judges(
        self, payload: dict[str, object], line_count: int
    ) -> tuple[list[JudgeResult], list[str]]:
        judge_service = self._judge
        assert judge_service is not None

        async def one(model: str) -> JudgeResult | ProviderError:
            try:
                return await judge_service.judge(
                    model=model,
                    payload=payload,
                    source_line_count=line_count,
                )
            except ProviderError as error:
                return error
            except Exception:
                return ProviderError("provider_unavailable")

        outcomes = await asyncio.gather(*(one(model) for model in self._judge_models))
        return (
            [item for item in outcomes if isinstance(item, JudgeResult)],
            [item.code for item in outcomes if isinstance(item, ProviderError)],
        )

    async def _run_adversarial(
        self,
        *,
        task: RegisteredTask,
        payload: dict[str, object],
        candidate_source: str,
    ) -> AdversarialResult:
        if task.reference_path is None:
            return _empty_adversarial(AIComponentStatus.SKIPPED, "reference_unavailable")
        if self._adversarial is None:
            return _empty_adversarial(AIComponentStatus.SKIPPED, "sandbox_unavailable")
        try:
            reference_source = task.reference_path.read_text(encoding="utf-8")
            return await self._adversarial.evaluate(
                payload=payload,
                task_id=task.specification.id,
                timeout_seconds=task.specification.timeout_seconds,
                candidate_source=candidate_source,
                reference_source=reference_source,
            )
        except ProviderError as error:
            return _empty_adversarial(AIComponentStatus.UNAVAILABLE, error.code)
        except OSError:
            return _empty_adversarial(AIComponentStatus.SKIPPED, "reference_unavailable")
        except Exception:
            return _empty_adversarial(AIComponentStatus.UNAVAILABLE, "adversarial_unavailable")

    def _assessment(
        self,
        *,
        status: AIStatus,
        reason: str | None,
        snapshot: EvaluationSnapshot,
        identity: AIIdentity,
        provenance: AIProvenance,
        ai_score: float | None = None,
        judge_score: float | None = None,
        judge_disputed: bool = False,
        judge_disagreement_spread: float | None = None,
        judge_results: list[JudgeResult] | None = None,
        adversarial_tests: AdversarialResult | None = None,
    ) -> AIAssessment:
        artifacts: dict[str, Any] = {
            "deterministic_reproducibility_fingerprint": snapshot.reproducibility_fingerprint,
            "identity": identity.model_dump(mode="json"),
            "status": status,
            "reason": reason,
            "judge_response_hashes": [item.raw_response_hash for item in judge_results or []],
            "adversarial_response_hash": (
                None if adversarial_tests is None else adversarial_tests.raw_response_hash
            ),
        }
        fingerprint = hashlib.sha256(canonical_json(artifacts).encode("utf-8")).hexdigest()
        return AIAssessment(
            status=status,
            reason=reason,
            ai_score=ai_score,
            judge_score=judge_score,
            judge_disputed=judge_disputed,
            judge_disagreement_spread=judge_disagreement_spread,
            judge_results=judge_results or [],
            adversarial_tests=adversarial_tests,
            provenance=provenance,
            ai_reproducibility_fingerprint=fingerprint,
        )

    async def close(self) -> None:
        if self._provider is not None:
            await self._provider.close()


def reference_fingerprint(task: RegisteredTask) -> str | None:
    if task.reference_path is None:
        return None
    try:
        return hashlib.sha256(task.reference_path.read_bytes()).hexdigest()
    except OSError:
        return None


def _provenance(identity: AIIdentity) -> AIProvenance:
    return AIProvenance(
        policy_version=identity.policy_version,
        judge_prompt_version=identity.judge_prompt_version,
        judge_prompt_hash=identity.judge_prompt_hash,
        adversarial_prompt_version=identity.adversarial_prompt_version,
        adversarial_prompt_hash=identity.adversarial_prompt_hash,
        provider_id=identity.provider_id,
        judge_models=list(identity.judge_models),
        adversarial_model=identity.adversarial_model,
        temperature=identity.temperature,
        top_p=identity.top_p,
        max_output_tokens=identity.max_output_tokens,
        reference_fingerprint=identity.reference_fingerprint,
    )


def _deterministic_evidence(snapshot: EvaluationSnapshot) -> dict[str, Any]:
    return {
        "score": snapshot.final_score,
        "score_breakdown": snapshot.score_breakdown.model_dump(mode="json"),
        "tests": snapshot.tests.model_dump(mode="json"),
        "oom_killed": snapshot.oom_killed,
        "execution_findings": [
            item.model_dump(mode="json") for item in snapshot.execution_findings
        ],
        "analysis_findings": [item.model_dump(mode="json") for item in snapshot.analysis_findings],
        "complexity": None if snapshot.complexity is None else snapshot.complexity.model_dump(),
    }


def _empty_adversarial(status: AIComponentStatus, reason: str) -> AdversarialResult:
    return AdversarialResult(
        status=status,
        reason=reason,
        generated=0,
        structurally_accepted=0,
        reference_valid=0,
        candidate_passed=0,
        candidate_failed=0,
    )


def _adversarial_reason(result: AdversarialResult | None) -> str:
    return (
        "adversarial_unavailable"
        if result is None
        else (result.reason or "adversarial_unavailable")
    )
