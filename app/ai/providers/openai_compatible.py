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

        if response.status_code == 429:
            raise ProviderError("provider_rate_limited", transient=True)
        if 500 <= response.status_code <= 599:
            raise ProviderError("provider_unavailable", transient=True)
        if response.status_code >= 400:
            raise ProviderError("provider_request_rejected")
        try:
            payload: object = json.loads(payload_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProviderError("malformed_provider_response") from error
        if not isinstance(payload, dict):
            raise ProviderError("malformed_provider_response")
        completion, envelope_type = _normalize_completion(payload)
        choices = completion["choices"]
        first_choice = choices[0]
        content = _response_content(completion)
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
                content_type="string",
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
        if not isinstance(data, dict) or not isinstance(data.get("choices"), list):
            raise ProviderError("malformed_provider_response")
        completion = data
        envelope_type = "data-wrapper"
        choices = data["choices"]
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("malformed_provider_response")
    return completion, envelope_type


def _response_content(completion: dict[str, Any]) -> str:
    choices = completion["choices"]
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ProviderError("malformed_provider_response")
    content = message.get("content")
    if not isinstance(content, str):
        code = "provider_refusal" if message.get("refusal") else "malformed_provider_response"
        raise ProviderError(code)
    return content


def _first_string(*values: object) -> str | None:
    return next((value for value in values if isinstance(value, str)), None)


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
