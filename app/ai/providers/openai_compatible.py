"""Bounded async adapter for the OpenAI-compatible chat-completions protocol."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import httpx

from app.ai.models import (
    ProviderResponse,
    ProviderResponseDiagnostics,
    ProviderUsage,
    StructuredLLMRequest,
)
from app.ai.prompts import canonical_json
from app.ai.providers.base import ProviderError


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        max_attempts: int,
        max_response_bytes: int,
        max_concurrent_requests: int | None = None,
        client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None] | None] | None = None,
    ) -> None:
        if max_concurrent_requests is not None and max_concurrent_requests <= 0:
            raise ValueError("max_concurrent_requests must be greater than zero")
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._max_response_bytes = max_response_bytes
        self._request_slots = (
            None if max_concurrent_requests is None else asyncio.Semaphore(max_concurrent_requests)
        )
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._sleeper = sleeper

    async def complete_structured(self, request: StructuredLLMRequest) -> ProviderResponse:
        return await self._complete(request, structured=True)

    async def complete_raw_source(self, request: StructuredLLMRequest) -> ProviderResponse:
        return await self._complete(request, structured=False)

    async def _complete(
        self, request: StructuredLLMRequest, *, structured: bool
    ) -> ProviderResponse:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": canonical_json(request.input_payload)},
            ],
            "temperature": request.temperature,
            "top_p": request.top_p,
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
        if structured:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": f"codejudge_{request.component}",
                    "strict": True,
                    "schema": request.response_schema,
                },
            }
        if request.seed is not None:
            body["seed"] = request.seed
        last_error = ProviderError("provider_unavailable", transient=True)
        for attempt in range(1, self._max_attempts + 1):
            try:
                if self._request_slots is None:
                    return await self._request(body)
                async with self._request_slots:
                    return await self._request(body)
            except ProviderError as error:
                last_error = error
                if not error.transient or attempt >= self._max_attempts:
                    raise
                await self._sleep(float(attempt))
        raise last_error

    async def _request(self, body: dict[str, Any]) -> ProviderResponse:
        started = time.monotonic()
        request = self._client.build_request(
            "POST",
            self._endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=self._timeout_seconds,
        )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._client.send(request, stream=True)
                try:
                    payload_bytes = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(payload_bytes) + len(chunk) > self._max_response_bytes:
                            raise ProviderError("provider_output_too_large")
                        payload_bytes.extend(chunk)
                finally:
                    await response.aclose()
        except (TimeoutError, httpx.TimeoutException) as error:
            raise ProviderError("provider_timeout", transient=True) from error
        except httpx.RequestError as error:
            raise ProviderError("provider_unavailable", transient=True) from error

        if response.status_code == 401:
            raise ProviderError("provider_unauthorized", http_status=401)
        if response.status_code == 403:
            raise ProviderError("provider_forbidden", http_status=403)
        if response.status_code == 404:
            raise ProviderError("provider_not_found", http_status=404)
        if response.status_code == 429:
            raise ProviderError("provider_rate_limited", transient=True, http_status=429)
        if 500 <= response.status_code <= 599:
            raise ProviderError(
                "provider_unavailable", transient=True, http_status=response.status_code
            )
        if response.status_code >= 400:
            raise ProviderError("provider_request_rejected", http_status=response.status_code)
        try:
            payload: object = json.loads(payload_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProviderError(
                "malformed_provider_response", detail_code="malformed_json"
            ) from error
        if not isinstance(payload, dict):
            raise ProviderError("malformed_provider_response", detail_code="unsupported_root_type")
        completion, envelope_type = _normalize_completion(payload)
        choices = completion["choices"]
        first_choice = choices[0]
        content, content_type = _response_content(completion)
        usage_raw = completion.get("usage")
        usage = usage_raw if isinstance(usage_raw, dict) else {}
        response_id = _first_string(completion.get("id"), payload.get("id"))
        response_model = _first_string(completion.get("model"), payload.get("model"))
        return ProviderResponse(
            content=content,
            response_id=response_id,
            usage=ProviderUsage(
                input_tokens=_optional_nonnegative_int(usage.get("prompt_tokens")),
                output_tokens=_optional_nonnegative_int(usage.get("completion_tokens")),
            ),
            latency_ms=max(0, round((time.monotonic() - started) * 1000)),
            diagnostics=ProviderResponseDiagnostics(
                http_status=response.status_code,
                envelope_type=envelope_type,
                choices_count=len(choices),
                finish_reason=(
                    first_choice.get("finish_reason")
                    if isinstance(first_choice.get("finish_reason"), str)
                    else None
                ),
                content_type=content_type,
                content_length=len(content.encode("utf-8")),
                usage_present=isinstance(usage_raw, dict),
                provider_response_model=response_model,
            ),
        )

    async def _sleep(self, delay: float) -> None:
        if self._sleeper is None:
            await asyncio.sleep(delay)
            return
        result = self._sleeper(delay)
        if result is not None:
            await result

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _normalize_completion(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], Literal["root", "data-wrapper"]]:
    choices = payload.get("choices")
    envelope_type: Literal["root", "data-wrapper"]
    if isinstance(choices, list):
        completion = payload
        envelope_type = "root"
    else:
        data = payload.get("data")
        if not isinstance(data, dict):
            detail_code = "missing_choices" if "choices" not in payload else "invalid_choices_type"
            raise ProviderError("malformed_provider_response", detail_code=detail_code)
        data_choices = data.get("choices")
        if not isinstance(data_choices, list):
            detail_code = "missing_choices" if "choices" not in data else "invalid_choices_type"
            raise ProviderError("malformed_provider_response", detail_code=detail_code)
        completion = data
        envelope_type = "data-wrapper"
        choices = data_choices
    if not choices:
        raise ProviderError("malformed_provider_response", detail_code="empty_choices")
    if not isinstance(choices[0], dict):
        raise ProviderError("malformed_provider_response", detail_code="invalid_choice")
    return completion, envelope_type


def _response_content(completion: dict[str, Any]) -> tuple[str, str]:
    choices = completion["choices"]
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        detail_code = "missing_message" if "message" not in choice else "invalid_message_type"
        raise ProviderError("malformed_provider_response", detail_code=detail_code)
    return _extract_assistant_text(message)


def _extract_assistant_text(message: dict[str, Any]) -> tuple[str, str]:
    """Extract only recognized final assistant text without exposing reasoning or tool data."""
    content_present = "content" in message
    content = message.get("content")
    if isinstance(content, str):
        if content == "":
            raise ProviderError("malformed_provider_response", detail_code="empty_content")
        return content, "string"
    if isinstance(content, list):
        if not content:
            raise ProviderError("malformed_provider_response", detail_code="empty_content")
        text_parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                raise ProviderError(
                    "malformed_provider_response", detail_code="unsupported_content_type"
                )
            part_type = part.get("type")
            if part_type == "refusal":
                raise ProviderError("provider_refusal", detail_code="refusal")
            if part_type not in {"text", "output_text"} or not isinstance(part.get("text"), str):
                raise ProviderError(
                    "malformed_provider_response", detail_code="unsupported_content_type"
                )
            text_parts.append(part["text"])
        text = "".join(text_parts)
        if text == "":
            raise ProviderError("malformed_provider_response", detail_code="empty_content")
        return text, "text-parts"
    refusal = message.get("refusal")
    if isinstance(refusal, str):
        raise ProviderError("provider_refusal", detail_code="refusal")
    if message.get("tool_calls") is not None or message.get("function_call") is not None:
        raise ProviderError("malformed_provider_response", detail_code="tool_call_only")
    if any(key in message for key in ("reasoning", "reasoning_content", "reasoning_details")):
        raise ProviderError("malformed_provider_response", detail_code="reasoning_only")
    if not content_present:
        raise ProviderError("malformed_provider_response", detail_code="missing_content")
    if content is None:
        raise ProviderError("malformed_provider_response", detail_code="null_content")
    raise ProviderError("malformed_provider_response", detail_code="unsupported_content_type")


def _first_string(*values: object) -> str | None:
    return next((value for value in values if isinstance(value, str)), None)


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
