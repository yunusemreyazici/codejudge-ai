from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.ai.providers.base import ProviderError
from app.benchmarks.models import (
    BenchmarkModelConfig,
    BenchmarkSample,
    BenchmarkSampleStatus,
    GenerationOutputMode,
)
from app.benchmarks.worker import (
    BenchmarkWorker,
    _candidate_evaluation_request,
    _generation_failure_code,
)
from app.tasks.registry import TaskRegistry
from tests.ai.fakes import FakeProvider


def _sample_and_config(
    output_mode: GenerationOutputMode,
) -> tuple[BenchmarkSample, BenchmarkModelConfig]:
    now = datetime.now(UTC)
    run_id = uuid4()
    config_id = uuid4()
    sample = BenchmarkSample(
        benchmark_sample_id=uuid4(),
        benchmark_run_id=run_id,
        model_config_id=config_id,
        evaluation_id=uuid4(),
        task_id="lru-cache",
        task_version="1.0",
        task_fingerprint="a" * 64,
        tests_fingerprint="b" * 64,
        task_weight=1,
        sample_index=1,
        status=BenchmarkSampleStatus.GENERATING,
        attempt_count=1,
        max_attempts=1,
        created_at=now,
        updated_at=now,
    )
    config = BenchmarkModelConfig(
        model_config_id=config_id,
        benchmark_run_id=run_id,
        ordinal=0,
        provider_id="fake",
        model="model-a",
        display_name="Model A",
        temperature=0,
        top_p=1,
        max_output_tokens=1000,
        output_mode=output_mode,
        request_timeout_seconds=120,
        coding_prompt_hash="c" * 64,
        model_configuration_fingerprint="d" * 64,
    )
    return sample, config


def _worker(provider: FakeProvider) -> BenchmarkWorker:
    return BenchmarkWorker(
        worker_id="transport-test",
        providers={"fake": provider},
        repository=None,  # type: ignore[arg-type]
        queue=None,  # type: ignore[arg-type]
        datasets=None,  # type: ignore[arg-type]
        tasks=None,  # type: ignore[arg-type]
        evaluations=None,  # type: ignore[arg-type]
        max_code_size=100_000,
        lease_seconds=10,
        retry_base_delay_seconds=0,
    )


@pytest.mark.parametrize(
    "source",
    [
        "def clean():\n    return 1\n",
        "\n\ndef indented():\n\tif True:\n\t\treturn 4\n\n",
        "```python\ndef fenced():\n    return 2\n```",
        "Here is the implementation:\ndef prose():\n    return 3\n",
    ],
)
async def test_raw_source_preserves_every_byte_as_generation_success(source: str) -> None:
    provider = FakeProvider()
    provider.add("coding_generation", "model-a", [source])
    sample, config = _sample_and_config(GenerationOutputMode.RAW_SOURCE)

    artifact = await _worker(provider)._generate(
        sample, config, TaskRegistry.default().get("lru-cache").specification
    )

    assert artifact.source == source
    assert artifact.source_size == len(source.encode("utf-8"))
    assert _candidate_evaluation_request(sample, artifact.source).code == source


@pytest.mark.parametrize("source", ["", " ", "\n", "\t", " \n\t\r\n"])
async def test_raw_source_blank_content_fails_before_evaluation(source: str) -> None:
    provider = FakeProvider()
    provider.add("coding_generation", "model-a", [source])
    sample, config = _sample_and_config(GenerationOutputMode.RAW_SOURCE)

    with pytest.raises(ProviderError, match="empty_output") as error:
        await _worker(provider)._generate(
            sample, config, TaskRegistry.default().get("lru-cache").specification
        )
    assert error.value.detail_code == "empty_output"


async def test_structured_mode_still_rejects_invalid_json() -> None:
    provider = FakeProvider()
    provider.add("coding_generation", "model-a", ["def raw(): pass"])
    sample, config = _sample_and_config(GenerationOutputMode.STRUCTURED_JSON)

    with pytest.raises(ProviderError, match="malformed_output"):
        await _worker(provider)._generate(
            sample, config, TaskRegistry.default().get("lru-cache").specification
        )


def test_provider_envelope_failure_is_not_candidate_malformed_output() -> None:
    assert _generation_failure_code("malformed_provider_response") == (
        "malformed_provider_response"
    )
