"""Strict structured LLM judging with deterministic CodeJudge aggregation."""

from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from app.ai.models import (
    JudgeFinding,
    JudgeOutput,
    JudgeResult,
    StructuredLLMRequest,
)
from app.ai.prompts import (
    JUDGE_PROMPT_HASH,
    JUDGE_PROMPT_VERSION,
    JUDGE_SYSTEM_PROMPT,
    rendered_input_hash,
)
from app.ai.providers.base import LLMProvider, ProviderError
from app.ai.scoring import calculate_judge_score


class JudgeService:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        provider_id: str,
        max_output_tokens: int,
        temperature: float,
        top_p: float,
    ) -> None:
        self._provider = provider
        self._provider_id = provider_id
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._top_p = top_p

    async def judge(
        self,
        *,
        model: str,
        payload: dict[str, object],
        source_line_count: int,
    ) -> JudgeResult:
        response = await self._provider.complete_structured(
            StructuredLLMRequest(
                component="judge",
                model=model,
                system_prompt=JUDGE_SYSTEM_PROMPT,
                input_payload=payload,
                response_schema=JudgeOutput.model_json_schema(),
                max_output_tokens=self._max_output_tokens,
                temperature=self._temperature,
                top_p=self._top_p,
            )
        )
        try:
            raw: object = json.loads(response.content)
            output = JudgeOutput.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as error:
            raise ProviderError("malformed_output") from error
        findings = [
            JudgeFinding(
                severity=finding.severity,
                category=finding.category,
                message=finding.message,
                line=(
                    finding.line
                    if finding.line is not None and finding.line <= source_line_count
                    else None
                ),
            )
            for finding in output.findings
        ]
        return JudgeResult(
            provider_id=self._provider_id,
            model=model,
            prompt_version=JUDGE_PROMPT_VERSION,
            prompt_hash=JUDGE_PROMPT_HASH,
            rendered_input_hash=rendered_input_hash(payload),
            score=calculate_judge_score(output),
            confidence=output.confidence,
            dimensions={
                "requirements_adherence": output.requirements_adherence,
                "logic_risk": output.logic_risk,
                "maintainability": output.maintainability,
                "edge_case_coverage": output.edge_case_coverage,
            },
            findings=findings,
            summary=output.summary,
            provider_response_id=response.response_id,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=response.latency_ms,
            raw_response_hash=hashlib.sha256(response.content.encode("utf-8")).hexdigest(),
        )
