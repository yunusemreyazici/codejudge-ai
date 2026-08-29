from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.benchmarks.exporting import _model_document
from app.benchmarks.models import (
    BenchmarkModelConfig,
    BenchmarkSample,
    BenchmarkSampleStatus,
    GeneratedSolutionArtifact,
    PricingSnapshot,
)
from app.benchmarks.pricing import calculate_generation_cost
from app.benchmarks.repositories import BenchmarkResultRow
from app.benchmarks.statistics import (
    build_leaderboard,
    confidence_interval_95,
    metric_summary,
    percentile_95,
)

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
    tests_passed: int | None = None,
    tests_failed: int | None = None,
    test_execution_seconds: float | None = None,
    evaluation_lifecycle_seconds: float | None = None,
    sample_index: int = 1,
    failure_code: str | None = None,
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
        sample_index=sample_index,
        status=status,
        attempt_count=1,
        max_attempts=3,
        failure_code=failure_code,
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
        tests_passed=tests_passed,
        tests_failed=tests_failed,
        test_execution_seconds=test_execution_seconds,
        evaluation_lifecycle_seconds=evaluation_lifecycle_seconds,
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


def test_student_t_interval_is_deterministic_and_unavailable_below_two_samples() -> None:
    assert confidence_interval_95([]) is None
    assert confidence_interval_95([85]) is None

    interval = confidence_interval_95([80, 85, 90])

    assert interval is not None
    assert interval.method == "student_t_two_sided_95"
    assert interval.sample_count == 3
    assert interval.lower == pytest.approx(72.5793, abs=0.0001)
    assert interval.upper == pytest.approx(97.4207, abs=0.0001)


def test_one_sample_per_task_does_not_relabel_cross_task_variation_as_stability() -> None:
    config = _config("single-sample-tasks")

    entry = build_leaderboard(
        [config],
        [
            _row(config, 0, task_id="task-a", tests_failed=1),
            _row(config, 100, task_id="task-b", tests_failed=0),
        ],
    )[0]

    assert entry.deterministic_scores.count == 2
    assert entry.deterministic_scores.mean == 50
    assert entry.deterministic_scores.standard_deviation is None
    assert entry.confidence_interval_95 is None
    assert entry.stability_label == "not_enough_samples"


def test_repeated_samples_are_aggregated_task_first_and_missing_samples_only_adjust_coverage() -> (
    None
):
    config = _config("task-first")
    rows = [
        _row(config, 100, task_id="task-a", sample_index=index, tests_failed=0)
        for index in range(1, 4)
    ]
    rows.extend(
        [
            _row(config, 0, task_id="task-b", sample_index=1, tests_failed=1),
            _row(
                config,
                None,
                task_id="task-b",
                sample_index=2,
                status=BenchmarkSampleStatus.GENERATION_FAILED,
            ),
            _row(
                config,
                None,
                task_id="task-b",
                sample_index=3,
                status=BenchmarkSampleStatus.GENERATION_FAILED,
            ),
        ]
    )

    entry = build_leaderboard([config], rows)[0]
    tasks = {task.task_id: task for task in entry.per_task}

    # Three observations from task A do not outweigh one completed observation from task B.
    assert entry.weighted_mean_score == 50
    assert entry.coverage == pytest.approx(4 / 6)
    assert entry.coverage_adjusted_deterministic_score == 50
    assert tasks["task-a"].scores.mean == 100
    assert tasks["task-a"].scores.standard_deviation == 0
    assert tasks["task-a"].correctness_consistency == "consistently_correct"
    assert tasks["task-b"].planned_samples == 3
    assert tasks["task-b"].completed_samples == 1
    assert tasks["task-b"].coverage == pytest.approx(1 / 3)
    assert tasks["task-b"].scores.standard_deviation is None
    assert tasks["task-b"].generation_failure_rate == pytest.approx(2 / 3)
    assert tasks["task-b"].correctness_consistency == "never_correct"
    assert entry.correctness_consistency.tasks_consistently_correct == 1
    assert entry.correctness_consistency.tasks_never_correct == 1
    assert entry.correctness_consistency.tasks_with_incomplete_coverage == 1
    assert entry.reliability.planned_samples == 6
    assert entry.reliability.successful_generations == 4
    assert entry.reliability.generation_failures == 2
    assert entry.reliability.completed_evaluations == 4
    assert entry.reliability.correct_evaluations == 3


def test_repeated_correctness_consistency_and_latency_distributions_are_explicit() -> None:
    config = _config("repeated")
    rows = [
        _row(
            config,
            80,
            task_id="variable",
            sample_index=1,
            tests_failed=0,
            latency=10,
            test_execution_seconds=1,
            evaluation_lifecycle_seconds=11,
        ),
        _row(
            config,
            100,
            task_id="variable",
            sample_index=2,
            tests_failed=1,
            latency=30,
            test_execution_seconds=3,
            evaluation_lifecycle_seconds=13,
        ),
        _row(
            config,
            90,
            task_id="variable",
            sample_index=3,
            tests_failed=0,
            latency=20,
            test_execution_seconds=2,
            evaluation_lifecycle_seconds=12,
        ),
        *[
            _row(
                config,
                None,
                task_id="missing",
                sample_index=index,
                status=BenchmarkSampleStatus.GENERATION_FAILED,
                failure_code="provider_timeout",
            )
            for index in range(1, 4)
        ],
    ]

    entry = build_leaderboard([config], rows)[0]
    variable = next(task for task in entry.per_task if task.task_id == "variable")

    assert variable.scores.standard_deviation == 10
    assert variable.correctness_pass_rate == pytest.approx(2 / 3)
    assert variable.end_to_end_success_rate == pytest.approx(2 / 3)
    assert variable.correctness_consistency == "sometimes_correct"
    assert entry.stability_label == "moderate"
    assert entry.generation_latency_distribution_ms.model_dump() == {
        "count": 3,
        "mean": 20,
        "median": 20,
        "standard_deviation": 10,
        "minimum": 10,
        "maximum": 30,
    }
    assert entry.test_execution_distribution_seconds.standard_deviation == 1
    assert entry.evaluation_lifecycle_distribution_seconds.standard_deviation == 1
    assert entry.reliability.provider_timeouts == 3
    assert entry.correctness_consistency.tasks_sometimes_correct == 1
    assert entry.correctness_consistency.tasks_without_completed_evaluations == 1


def test_leaderboard_keeps_score_coverage_weighting_ai_and_cost_separate() -> None:
    good = _config("good")
    flaky = _config("flaky", 1).model_copy(update={"benchmark_run_id": good.benchmark_run_id})
    rows = [
        _row(
            good,
            100,
            weight=2,
            cost=Decimal("0.10"),
            currency="USD",
            latency=10,
            tests_failed=0,
        ),
        _row(
            good,
            50,
            task_id="other",
            weight=1,
            cost=Decimal("0.20"),
            currency="EUR",
            latency=30,
            tests_failed=1,
        ),
        _row(flaky, 100, tests_failed=0),
        _row(flaky, None, status=BenchmarkSampleStatus.GENERATION_FAILED),
        _row(flaky, None, status=BenchmarkSampleStatus.SKIPPED),
    ]

    leaderboard = build_leaderboard([good, flaky], rows)

    assert [entry.model for entry in leaderboard] == ["flaky", "good"]
    first = leaderboard[1]
    assert first.weighted_mean_score == pytest.approx(250 / 3)
    assert first.deterministic_scores.mean == 75
    assert first.coverage == 1
    assert first.perfect_deterministic_score_rate == 0.5
    assert first.correctness_pass_rate == 0.5
    assert first.end_to_end_success_rate == 0.5
    assert first.coverage_adjusted_deterministic_score == pytest.approx(250 / 3)
    assert first.mean_ai_score == 80
    assert first.generation_costs == {"EUR": Decimal("0.20"), "USD": Decimal("0.10")}
    assert leaderboard[0].coverage == pytest.approx(1 / 3)
    assert leaderboard[0].successful_generation_rate == pytest.approx(1 / 3)
    assert leaderboard[0].generation_failure_rate == pytest.approx(1 / 3)
    assert leaderboard[0].coverage_adjusted_deterministic_score == pytest.approx(100 / 3)


def test_observed_seven_sample_shape_keeps_primary_quality_and_coverage_separate() -> None:
    complete = _config("complete")
    partial = _config("partial", 1).model_copy(
        update={"benchmark_run_id": complete.benchmark_run_id}
    )
    complete_scores = [96.50, 87.73, 96.75, 95.20, 90.13, 92.72, 91.13]
    partial_scores = [76.40, 58.27, 99.20, 89.03, 90.63]
    rows = [
        *[_row(complete, score, tests_failed=0) for score in complete_scores],
        *[_row(partial, score, tests_failed=0) for score in partial_scores],
        _row(partial, None, status=BenchmarkSampleStatus.GENERATION_FAILED),
        _row(partial, None, status=BenchmarkSampleStatus.GENERATION_FAILED),
    ]

    by_model = {entry.model: entry for entry in build_leaderboard([complete, partial], rows)}

    assert by_model["complete"].weighted_mean_score == pytest.approx(92.88)
    assert by_model["partial"].weighted_mean_score == pytest.approx(82.706)
    assert by_model["complete"].coverage == 1
    assert by_model["partial"].coverage == pytest.approx(5 / 7)
    assert by_model["complete"].coverage_adjusted_deterministic_score == pytest.approx(92.88)
    assert by_model["partial"].coverage_adjusted_deterministic_score == pytest.approx(59.0757142857)


def test_correctness_perfect_score_end_to_end_and_timings_have_distinct_semantics() -> None:
    config = _config("semantic-cases")
    rows = [
        _row(
            config,
            96.5,
            tests_passed=6,
            tests_failed=0,
            test_execution_seconds=2,
            evaluation_lifecycle_seconds=200,
        ),
        _row(
            config,
            92,
            tests_passed=5,
            tests_failed=1,
            test_execution_seconds=4,
            evaluation_lifecycle_seconds=260,
        ),
        _row(config, None, status=BenchmarkSampleStatus.GENERATION_FAILED),
    ]

    entry = build_leaderboard([config], rows)[0]

    assert entry.weighted_mean_score == pytest.approx(94.25)
    assert entry.coverage == pytest.approx(2 / 3)
    assert entry.coverage_adjusted_deterministic_score == pytest.approx(188.5 / 3)
    assert entry.correctness_pass_rate == 0.5
    assert entry.end_to_end_success_rate == pytest.approx(1 / 3)
    assert entry.perfect_deterministic_score_rate == 0
    assert entry.mean_test_execution_seconds == 3
    assert entry.median_test_execution_seconds == 3
    assert entry.p95_test_execution_seconds == 4
    assert entry.mean_evaluation_lifecycle_seconds == 230


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


def test_repeated_cost_distribution_uses_only_complete_known_cost_observations() -> None:
    config = _config("costed")
    rows = [
        _row(
            config,
            score,
            sample_index=index,
            cost=cost,
            currency="USD",
            tests_failed=0,
        )
        for index, (score, cost) in enumerate(
            [(80, Decimal("0.01")), (90, Decimal("0.02")), (100, Decimal("0.03"))],
            1,
        )
    ]
    entry = build_leaderboard([config], rows)[0]

    document = _model_document(config, rows, entry)

    assert document["actual_generation_costs"] == {"USD": Decimal("0.06")}
    assert document["mean_cost_per_planned_sample"] == {"USD": Decimal("0.02")}
    assert document["cost_per_successful_generation"] == {"USD": Decimal("0.02")}
    assert document["cost_per_correct_evaluation"] == {"USD": Decimal("0.02")}
    assert document["generation_cost_distributions"]["USD"] == {
        "count": 3,
        "mean": 0.02,
        "median": 0.02,
        "standard_deviation": pytest.approx(0.01),
        "minimum": 0.01,
        "maximum": 0.03,
    }

    incomplete_rows = [
        rows[0],
        _row(config, 90, sample_index=2, tests_failed=0),
    ]
    incomplete = _model_document(
        config,
        incomplete_rows,
        build_leaderboard([config], incomplete_rows)[0],
    )
    assert incomplete["generation_cost_distribution_status"] == ("unknown_incomplete_cost_coverage")
    assert incomplete["generation_cost_distributions"] == {}
    assert incomplete["mean_cost_per_planned_sample"] is None
    assert incomplete["mean_cost_per_planned_sample_status"] == ("unknown_incomplete_cost_coverage")
