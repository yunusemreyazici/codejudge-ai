from __future__ import annotations

import json

import httpx
import pytest

from app.ai.models import StructuredLLMRequest
from app.ai.providers.base import ProviderError
from app.ai.providers.openai_compatible import OpenAICompatibleProvider

pytestmark = pytest.mark.ai


def _request() -> StructuredLLMRequest:
    return StructuredLLMRequest(
        component="judge",
        model="model-a",
        system_prompt="system",
        input_payload={"untrusted_candidate_source": "secret-like-code"},
        response_schema={"type": "object"},
        max_output_tokens=100,
        temperature=0,
        top_p=1,
    )


def _response(status: int = 200, *, content: str = "{}") -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "id": "response-1",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        },
    )


async def test_valid_response_usage_and_authentication_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _response(content='{"ok":true}')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="top-secret-key",
        timeout_seconds=1,
        max_attempts=2,
        max_response_bytes=4096,
        client=client,
    )
    response = await provider.complete_structured(_request())

    assert response.content == '{"ok":true}'
    assert response.response_id == "response-1"
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 7
    assert seen[0].headers["authorization"] == "Bearer top-secret-key"
    body = json.loads(seen[0].content)
    assert body["max_tokens"] == 100
    assert body["response_format"]["type"] == "json_schema"
    await client.aclose()


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_status_retries_once(status: int) -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(status) if calls == 1 else _response()

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout_seconds=1,
        max_attempts=2,
        max_response_bytes=4096,
        client=client,
        sleeper=sleeper,
    )
    await provider.complete_structured(_request())
    assert calls == 2
    assert sleeps == [1.0]
    await client.aclose()


async def test_nonretryable_400_is_sanitized() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: _response(400)))
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout_seconds=1,
        max_attempts=2,
        max_response_bytes=4096,
        client=client,
    )
    with pytest.raises(ProviderError, match="provider_request_rejected"):
        await provider.complete_structured(_request())
    await client.aclose()


async def test_timeout_is_sanitized_and_bounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("raw provider detail", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout_seconds=1,
        max_attempts=1,
        max_response_bytes=4096,
        client=client,
    )
    with pytest.raises(ProviderError, match="provider_timeout"):
        await provider.complete_structured(_request())
    await client.aclose()


async def test_oversized_http_response_is_rejected_before_provider_json_parsing() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: _response(content="x" * 1000))
    )
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout_seconds=1,
        max_attempts=1,
        max_response_bytes=100,
        client=client,
    )
    with pytest.raises(ProviderError, match="provider_output_too_large"):
        await provider.complete_structured(_request())
    await client.aclose()


async def test_malformed_provider_json_is_rejected() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"not-json"))
    )
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout_seconds=1,
        max_attempts=1,
        max_response_bytes=4096,
        client=client,
    )
    with pytest.raises(ProviderError, match="malformed_provider_response"):
        await provider.complete_structured(_request())
    await client.aclose()


async def test_provider_refusal_is_sanitized() -> None:
    response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": None, "refusal": "unsafe"}}]},
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response))
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout_seconds=1,
        max_attempts=1,
        max_response_bytes=4096,
        client=client,
    )
    with pytest.raises(ProviderError, match="provider_refusal"):
        await provider.complete_structured(_request())
    await client.aclose()
