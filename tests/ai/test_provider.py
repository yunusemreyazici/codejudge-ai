from __future__ import annotations

import asyncio
import json
from typing import Any

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


def _wrapped_response(*, content: str = "print('hello')") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "generationId": "gateway-generation-1",
            "provider_metadata": {"gateway": "extension"},
            "data": {
                "model": "provider-resolved-model",
                "choices": [
                    {
                        "message": {
                            "content": content,
                            "reasoning": "extension",
                            "reasoning_details": [{"type": "extension"}],
                        },
                        "finish_reason": "stop",
                        "native_finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "cost": 0.00008654,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            },
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
    assert body["stream"] is False
    assert body["response_format"]["type"] == "json_schema"
    assert response.diagnostics is not None
    assert response.diagnostics.envelope_type == "root"
    await client.aclose()


@pytest.mark.parametrize("wrapped", [False, True])
async def test_root_and_data_wrapped_responses_normalize_identically(wrapped: bool) -> None:
    response = (
        _wrapped_response()
        if wrapped
        else httpx.Response(
            200,
            json={
                "model": "provider-resolved-model",
                "choices": [{"message": {"content": "print('hello')"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response))
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout_seconds=30,
        max_attempts=1,
        max_response_bytes=4096,
        client=client,
    )

    normalized = await provider.complete_raw_source(_request())

    assert normalized.content == "print('hello')"
    assert normalized.usage.input_tokens == 10
    assert normalized.usage.output_tokens == 5
    assert normalized.diagnostics is not None
    assert normalized.diagnostics.envelope_type == ("data-wrapper" if wrapped else "root")
    assert normalized.diagnostics.choices_count == 1
    assert normalized.diagnostics.finish_reason == "stop"
    assert normalized.diagnostics.usage_present is True
    assert normalized.diagnostics.provider_response_model == "provider-resolved-model"
    await client.aclose()


async def test_raw_source_contract_omits_schema_and_preserves_content() -> None:
    seen: list[dict[str, object]] = []
    exact_source = "```python\nprint('kept exactly')\n```\nSome prose."

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _response(content=exact_source)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout_seconds=120,
        max_attempts=1,
        max_response_bytes=4096,
        client=client,
    )

    response = await provider.complete_raw_source(_request())

    assert response.content == exact_source
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 7
    assert seen == [
        {
            "model": "model-a",
            "messages": [
                {"role": "system", "content": "system"},
                {
                    "role": "user",
                    "content": '{"untrusted_candidate_source":"secret-like-code"}',
                },
            ],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 100,
            "stream": False,
        }
    ]
    await client.aclose()


async def test_cline_wrapped_fenced_source_is_preserved_exactly() -> None:
    exact_source = '```python\nprint("hello")\n```'
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: _wrapped_response(content=exact_source))
    )
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout_seconds=120,
        max_attempts=1,
        max_response_bytes=4096,
        client=client,
    )

    response = await provider.complete_raw_source(_request())

    assert response.content == exact_source
    assert response.diagnostics is not None
    assert response.diagnostics.content_length == len(exact_source.encode("utf-8"))
    await client.aclose()


async def test_optional_generation_seed_is_forwarded_without_claiming_determinism() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _response()

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout_seconds=30,
        max_attempts=1,
        max_response_bytes=4096,
        client=client,
    )
    await provider.complete_structured(_request().model_copy(update={"seed": 42}))
    assert seen[0]["seed"] == 42
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
        timeout_seconds=30,
        max_attempts=1,
        max_response_bytes=4096,
        client=client,
    )
    with pytest.raises(ProviderError, match="provider_timeout"):
        await provider.complete_structured(_request())
    await client.aclose()


async def test_configured_timeout_reaches_httpx_request() -> None:
    seen_timeout: list[dict[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_timeout.append(request.extensions["timeout"])
        return _response()

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout_seconds=120,
        max_attempts=1,
        max_response_bytes=4096,
        client=client,
    )

    await provider.complete_raw_source(_request())

    assert seen_timeout == [{"connect": 120.0, "read": 120.0, "write": 120.0, "pool": 120.0}]
    await client.aclose()


async def test_provider_concurrency_limit_is_shared_across_models() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    active = 0
    maximum_active = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        entered.set()
        await release.wait()
        active -= 1
        return _response(content="print('ok')")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout_seconds=30,
        max_attempts=1,
        max_response_bytes=4096,
        max_concurrent_requests=1,
        client=client,
    )
    requests = [_request().model_copy(update={"model": model}) for model in ("model-a", "model-b")]
    tasks = [asyncio.create_task(provider.complete_raw_source(request)) for request in requests]

    await entered.wait()
    await asyncio.sleep(0)
    assert maximum_active == 1
    release.set()
    await asyncio.gather(*tasks)

    assert maximum_active == 1
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


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": {}},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {"content": 123}}]},
    ],
)
async def test_broken_provider_envelopes_use_provider_error_taxonomy(
    payload: dict[str, Any],
) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        api_key="secret",
        timeout_seconds=30,
        max_attempts=1,
        max_response_bytes=4096,
        client=client,
    )

    with pytest.raises(ProviderError, match="malformed_provider_response"):
        await provider.complete_raw_source(_request())
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
