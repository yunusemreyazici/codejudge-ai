"""Create the configured supplemental AI evaluation service."""

from __future__ import annotations

from app.ai.adversarial import AdversarialService
from app.ai.judge import JudgeService
from app.ai.providers.base import LLMProvider
from app.ai.providers.openai_compatible import OpenAICompatibleProvider
from app.ai.sandbox import AdversarialSandbox, DockerAdversarialSandbox
from app.ai.service import AIService
from app.core.config import Settings
from app.runners.base import CodeRunner
from app.runners.docker_runner import DockerPythonRunner


def create_ai_service(
    settings: Settings,
    runner: CodeRunner,
    *,
    provider: LLMProvider | None = None,
    adversarial_sandbox: AdversarialSandbox | None = None,
) -> AIService:
    resolved_provider = provider
    if settings.llm_enabled and resolved_provider is None:
        if settings.llm_base_url is None or settings.llm_api_key is None:
            raise ValueError("LLM provider configuration is incomplete")
        resolved_provider = OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout_seconds=settings.llm_timeout_seconds,
            max_attempts=settings.llm_max_attempts,
            max_response_bytes=settings.llm_max_response_bytes,
        )
    sandbox = adversarial_sandbox
    if sandbox is None and isinstance(runner, DockerPythonRunner):
        sandbox = DockerAdversarialSandbox(runner)
    judge = None
    adversarial = None
    if settings.llm_enabled and resolved_provider is not None:
        judge = JudgeService(
            resolved_provider,
            provider_id=settings.llm_provider_id,
            max_output_tokens=settings.llm_max_output_tokens,
            temperature=settings.llm_temperature,
            top_p=settings.llm_top_p,
        )
        if settings.llm_adversarial_model is not None and sandbox is not None:
            adversarial = AdversarialService(
                resolved_provider,
                sandbox,
                provider_id=settings.llm_provider_id,
                model=settings.llm_adversarial_model,
                max_tests=settings.llm_max_adversarial_tests,
                max_output_tokens=settings.llm_max_output_tokens,
                temperature=settings.llm_temperature,
                top_p=settings.llm_top_p,
            )
    return AIService(
        enabled=settings.llm_enabled,
        provider=resolved_provider,
        provider_id=settings.llm_provider_id,
        judge_models=settings.llm_judge_models,
        adversarial_model=settings.llm_adversarial_model,
        judge_service=judge,
        adversarial_service=adversarial,
        max_input_bytes=settings.llm_max_input_bytes,
        max_output_tokens=settings.llm_max_output_tokens,
        temperature=settings.llm_temperature,
        top_p=settings.llm_top_p,
        disagreement_threshold=settings.llm_disagreement_threshold,
    )
