"""Bounded async adapter for the OpenAI-compatible chat-completions protocol."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.ai.models import ProviderResponse, ProviderUsage, StructuredLLMRequest
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
        client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None] | None] | None = None,
    ) -> None:
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._max_response_bytes = max_response_bytes
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._sleeper = sleeper

    async def complete_structured(self, request: StructuredLLMRequest) -> ProviderResponse:
        body = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": canonical_json(request.input_payload)},
            ],
            "temperature": request.temperature,
            "top_p": request.top_p,
            "max_tokens": request.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"codejudge_{request.component}",
                    "strict": True,
                    "schema": request.response_schema,
                },
            },
        }
        last_error = ProviderError("provider_unavailable", transient=True)
        for attempt in range(1, self._max_attempts + 1):
            try:
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
        content = _response_content(payload)
        usage_raw = payload.get("usage")
        usage = usage_raw if isinstance(usage_raw, dict) else {}
        return ProviderResponse(
            content=content,
            response_id=payload.get("id") if isinstance(payload.get("id"), str) else None,
            usage=ProviderUsage(
                input_tokens=_optional_nonnegative_int(usage.get("prompt_tokens")),
                output_tokens=_optional_nonnegative_int(usage.get("completion_tokens")),
            ),
            latency_ms=max(0, round((time.monotonic() - started) * 1000)),
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


def _response_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("malformed_provider_response")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ProviderError("malformed_provider_response")
    content = message.get("content")
    if not isinstance(content, str):
        raise ProviderError("provider_refusal" if message.get("refusal") else "malformed_output")
    return content


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
