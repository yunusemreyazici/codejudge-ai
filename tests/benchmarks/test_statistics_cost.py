from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.benchmarks.models import (
    BenchmarkModelConfig,
    BenchmarkSample,
    BenchmarkSampleStatus,
    GeneratedSolutionArtifact,
    PricingSnapshot,
)
from app.benchmarks.pricing import calculate_generation_cost
from app.benchmarks.repositories import BenchmarkResultRow
from app.benchmarks.statistics import build_leaderboard, metric_summary, percentile_95

NOW = datetime(2026, 8, 28, tzinfo=UTC)


def _config(name: str, ordinal: int = 0) -> BenchmarkModelConfig:
    run_id = uuid4()
    return BenchmarkModelConfig(
        model_config_id=uuid4(),
        benchmark_run_id=run_id,
        ordinal=ordinal,
        provider_id="fake",
        model=name,
        display_name=name,
        temperature=0,
        top_p=1,
        max_output_tokens=100,
        coding_prompt_hash="a" * 64,
        model_configuration_fingerprint=("b" if name == "good" else "c") * 64,
    )


def _row(
    config: BenchmarkModelConfig,
    score: float | None,
    *,
    status: BenchmarkSampleStatus = BenchmarkSampleStatus.COMPLETED,
    task_id: str = "lru-cache",
    weight: float = 1,
    cost: Decimal | None = None,
    currency: str | None = None,
    latency: int = 10,
) -> BenchmarkResultRow:
    sample_id = uuid4()
    sample = BenchmarkSample(
        benchmark_sample_id=sample_id,
        benchmark_run_id=config.benchmark_run_id,
        model_config_id=config.model_config_id,
        evaluation_id=uuid4(),
        task_id=task_id,
        task_version="1",
        task_fingerprint="d" * 64,
        tests_fingerprint="e" * 64,
        task_weight=weight,
        sample_index=1,
        status=status,
        attempt_count=1,
        max_attempts=3,
        evaluation_duration_seconds=0.5 if score is not None else None,
        created_at=NOW,
        updated_at=NOW,
        completed_at=NOW,
    )
    artifact = None
    if status not in {
        BenchmarkSampleStatus.GENERATION_FAILED,
        BenchmarkSampleStatus.SKIPPED,
    }:
        artifact = GeneratedSolutionArtifact(
            benchmark_sample_id=sample_id,
            source="x = 1\n",
            source_hash="f" * 64,
            source_size=6,
            input_tokens=10,
            output_tokens=20,
            generation_latency_ms=latency,
            generation_cost=cost,
            currency=currency,
            created_at=NOW,
        )
    return BenchmarkResultRow(
        sample=sample,
        config=config,
        artifact=artifact,
        deterministic_score=score,
        ai_score=80 if score is not None else None,
        judge_score=75 if score is not None else None,
        adversarial_robustness=90 if score is not None else None,
        ai_status="completed" if score is not None else None,
    )


def test_metric_summary_handles_zero_one_and_many_without_nan() -> None:
    assert metric_summary([]).model_dump() == {
        "count": 0,
        "mean": None,
        "median": None,
        "standard_deviation": None,
        "minimum": None,
        "maximum": None,
    }
    assert metric_summary([5]).standard_deviation is None
    summary = metric_summary([0, 50, 100])
    assert summary.mean == 50
    assert summary.median == 50
    assert summary.standard_deviation == 50
    assert percentile_95(list(range(1, 21))) == 19


def test_leaderboard_keeps_score_coverage_weighting_ai_and_cost_separate() -> None:
    good = _config("good")
    flaky = _config("flaky", 1).model_copy(update={"benchmark_run_id": good.benchmark_run_id})
    rows = [
        _row(good, 100, weight=2, cost=Decimal("0.10"), currency="USD", latency=10),
        _row(good, 50, task_id="other", weight=1, cost=Decimal("0.20"), currency="EUR", latency=30),
        _row(flaky, 100),
        _row(flaky, None, status=BenchmarkSampleStatus.GENERATION_FAILED),
        _row(flaky, None, status=BenchmarkSampleStatus.SKIPPED),
    ]

    leaderboard = build_leaderboard([good, flaky], rows)

    assert [entry.model for entry in leaderboard] == ["flaky", "good"]
    first = leaderboard[1]
    assert first.weighted_mean_score == pytest.approx(250 / 3)
    assert first.deterministic_scores.mean == 75
    assert first.coverage == 1
    assert first.pass_rate == 0.5
    assert first.mean_ai_score == 80
    assert first.generation_costs == {"EUR": Decimal("0.20"), "USD": Decimal("0.10")}
    assert leaderboard[0].coverage == pytest.approx(1 / 3)
    assert leaderboard[0].successful_generation_rate == pytest.approx(1 / 3)
    assert leaderboard[0].generation_failure_rate == pytest.approx(1 / 3)


def test_cost_requires_pricing_and_complete_usage_and_preserves_currency() -> None:
    pricing = PricingSnapshot(
        pricing_version="fake-2026-08",
        input_cost_per_million_tokens=Decimal("2"),
        output_cost_per_million_tokens=Decimal("8"),
        currency="usd",
    )

    assert calculate_generation_cost(pricing, 100, 50) == Decimal("0.000600000000")
    assert pricing.currency == "USD"
    assert calculate_generation_cost(None, 100, 50) is None
    assert calculate_generation_cost(pricing, None, 50) is None
