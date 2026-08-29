from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml  # type: ignore[import-untyped]

from app.benchmarks.cli import _probe, build_parser
from app.benchmarks.run_config import load_benchmark_config

EXAMPLE = Path("benchmark-configs/real-smoke.example.yaml")


async def test_probe_makes_exactly_one_sanitized_configured_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = load_benchmark_config(EXAMPLE).model_dump(mode="json")
    payload["models"] = [payload["models"][0]]
    payload["providers"] = {"provider-a": payload["providers"]["provider-a"]}
    payload["providers"]["provider-a"].update(
        {
            "output_mode": "raw_source",
            "request_timeout_seconds": 120,
            "max_concurrent_requests": 1,
        }
    )
    payload["pricing"] = {"provider-a/model-a": payload["pricing"]["provider-a/model-a"]}
    config_path = tmp_path / "probe.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "model": "resolved-provider-model",
                    "choices": [
                        {
                            "message": {"content": "print('probe')"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await _probe(
            config_path,
            model_name="model-a",
            show_content=False,
            environment={
                "CODEJUDGE_PROVIDER_A_BASE_URL": "https://provider.invalid/v1",
                "CODEJUDGE_PROVIDER_A_API_KEY": "probe-secret-key",
            },
            client=client,
        )
    finally:
        await client.aclose()

    output = capsys.readouterr().out
    assert result == 0
    assert len(seen) == 1
    request_body = json.loads(seen[0].content)
    assert request_body["stream"] is False
    assert "response_format" not in request_body
    assert seen[0].extensions["timeout"]["read"] == 120
    assert "HTTP status: 200" in output
    assert "Envelope type: data-wrapper" in output
    assert "Choices count: 1" in output
    assert "Finish reason: stop" in output
    assert "Content type: string" in output
    assert "Content length: 14" in output
    assert "Usage presence: yes" in output
    assert "Provider response model: resolved-provider-model" in output
    assert "print('probe')" not in output
    assert "probe-secret-key" not in output
    assert "You are solving" not in output


def test_probe_parser_requires_an_explicit_model() -> None:
    arguments = build_parser().parse_args(
        ["probe", str(EXAMPLE), "--model", "model-a", "--show-content"]
    )

    assert arguments.command == "probe"
    assert arguments.model == "model-a"
    assert arguments.show_content is True
