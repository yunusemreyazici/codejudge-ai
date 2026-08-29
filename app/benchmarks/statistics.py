"""Transparent benchmark aggregations with nulls instead of NaN."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import Literal
from uuid import UUID

from app.benchmarks.models import (
    BenchmarkModelConfig,
    BenchmarkSampleStatus,
    ConfidenceInterval95,
    CorrectnessConsistencySummary,
    LeaderboardEntry,
    MetricSummary,
    PerTaskMetrics,
    ReliabilitySummary,
)
from app.benchmarks.repositories import BenchmarkResultRow


def metric_summary(values: list[float]) -> MetricSummary:
    finite_values = [float(value) for value in values if math.isfinite(float(value))]
    if not finite_values:
        return MetricSummary(count=0)
    return MetricSummary(
        count=len(finite_values),
        mean=statistics.fmean(finite_values),
        median=statistics.median(finite_values),
        standard_deviation=(statistics.stdev(finite_values) if len(finite_values) > 1 else None),
        minimum=min(finite_values),
        maximum=max(finite_values),
    )


def confidence_interval_95(values: list[float]) -> ConfidenceInterval95 | None:
    """Two-sided Student-t interval for the observed arithmetic mean."""
    summary = metric_summary(values)
    if summary.count < 2 or summary.mean is None or summary.standard_deviation is None:
        return None
    critical = _student_t_critical_975(summary.count - 1)
    margin = critical * summary.standard_deviation / math.sqrt(summary.count)
    return ConfidenceInterval95(
        sample_count=summary.count,
        lower=summary.mean - margin,
        upper=summary.mean + margin,
    )


def percentile_95(values: list[float]) -> float | None:
    """Nearest-rank p95: sorted_values[ceil(0.95*n)-1]."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def build_leaderboard(
    configs: Sequence[BenchmarkModelConfig], samples: Sequence[BenchmarkResultRow]
) -> list[LeaderboardEntry]:
    grouped: dict[UUID, list[BenchmarkResultRow]] = defaultdict(list)
    for sample in samples:
        grouped[sample.model_config_id].append(sample)
    unranked = [_entry(config, grouped.get(config.model_config_id, [])) for config in configs]
    unranked.sort(key=_rank_key)
    return [entry.model_copy(update={"rank": rank}) for rank, entry in enumerate(unranked, 1)]


def _entry(config: BenchmarkModelConfig, samples: list[BenchmarkResultRow]) -> LeaderboardEntry:
    planned = len(samples)
    evaluated = [item for item in samples if item.deterministic_score is not None]
    scores = [
        float(item.deterministic_score)
        for item in evaluated
        if item.deterministic_score is not None
    ]
    task_groups: dict[str, list[BenchmarkResultRow]] = defaultdict(list)
    for item in samples:
        task_groups[item.task_id].append(item)
    per_task = [_task_metrics(task_id, task_groups[task_id]) for task_id in sorted(task_groups)]
    task_weights = {task_id: task_groups[task_id][0].task_weight for task_id in sorted(task_groups)}
    weighted_denominator = sum(
        task_weights[task.task_id] for task in per_task if task.scores.mean is not None
    )
    weighted_mean = (
        sum(
            float(task.scores.mean) * task_weights[task.task_id]
            for task in per_task
            if task.scores.mean is not None
        )
        / weighted_denominator
        if weighted_denominator > 0
        else None
    )
    planned_weight = sum(task_weights.values())
    coverage_adjusted_score = (
        sum(
            Decimal(str(task.coverage_adjusted_deterministic_score))
            * Decimal(str(task_weights[task.task_id]))
            for task in per_task
            if task.coverage_adjusted_deterministic_score is not None
        )
        / Decimal(str(planned_weight))
        if planned_weight > 0
        else None
    )
    generated = [item for item in samples if item.artifact is not None]
    ai_scores = [float(item.ai_score) for item in samples if item.ai_score is not None]
    judge_scores = [float(item.judge_score) for item in samples if item.judge_score is not None]
    adversarial = [
        float(item.adversarial_robustness)
        for item in samples
        if item.adversarial_robustness is not None
    ]
    generation_latencies = [
        float(item.generation_latency_ms)
        for item in samples
        if item.generation_latency_ms is not None
    ]
    evaluation_lifecycle_durations = [
        item.evaluation_lifecycle_seconds
        for item in samples
        if item.evaluation_lifecycle_seconds is not None
    ]
    test_execution_durations = [
        item.test_execution_seconds for item in samples if item.test_execution_seconds is not None
    ]
    costs: dict[str, Decimal] = defaultdict(Decimal)
    for item in samples:
        if item.generation_cost is not None and item.currency is not None:
            costs[item.currency] += item.generation_cost
    perfect_score_rate = sum(score == 100 for score in scores) / len(scores) if scores else None
    correctness_passes = sum(item.tests_failed == 0 for item in evaluated)
    end_to_end_successes = sum(
        item.artifact is not None and item.tests_failed == 0 for item in evaluated
    )
    correctness_pass_rate = correctness_passes / len(evaluated) if evaluated else None
    consistency = CorrectnessConsistencySummary(
        tasks_consistently_correct=sum(
            task.correctness_consistency == "consistently_correct" for task in per_task
        ),
        tasks_sometimes_correct=sum(
            task.correctness_consistency == "sometimes_correct" for task in per_task
        ),
        tasks_never_correct=sum(
            task.correctness_consistency == "never_correct" for task in per_task
        ),
        tasks_with_incomplete_coverage=sum(not task.coverage_complete for task in per_task),
        tasks_without_completed_evaluations=sum(task.completed_samples == 0 for task in per_task),
    )
    failure_codes = [item.sample.failure_code for item in samples]
    reliability = ReliabilitySummary(
        planned_samples=planned,
        successful_generations=len(generated),
        generation_failures=sum(
            item.status is BenchmarkSampleStatus.GENERATION_FAILED for item in samples
        ),
        completed_evaluations=len(evaluated),
        correct_evaluations=correctness_passes,
        end_to_end_successes=end_to_end_successes,
        provider_unavailable=failure_codes.count("provider_unavailable"),
        provider_timeouts=failure_codes.count("provider_timeout"),
        provider_rate_limits=failure_codes.count("provider_rate_limited"),
        provider_refusals=failure_codes.count("provider_refusal"),
        malformed_responses=sum(
            code in {"malformed_output", "malformed_provider_response"} for code in failure_codes
        ),
    )
    score_distribution = metric_summary(scores)
    repeated_plan = bool(per_task) and all(task.planned_samples >= 2 for task in per_task)
    if not repeated_plan:
        # Cross-task dispersion is not repeated-sample stability. Preserve the other descriptive
        # fields, but do not manufacture uncertainty for a one-sample-per-task run.
        score_distribution = score_distribution.model_copy(update={"standard_deviation": None})
    return LeaderboardEntry(
        rank=1,
        model_config_id=config.model_config_id,
        provider_id=config.provider_id,
        model=config.model,
        display_name=config.display_name,
        model_configuration_fingerprint=config.model_configuration_fingerprint,
        weighted_mean_score=weighted_mean,
        deterministic_scores=score_distribution,
        confidence_interval_95=(confidence_interval_95(scores) if repeated_plan else None),
        stability_label=_stability_label(score_distribution),
        correctness_consistency=consistency,
        reliability=reliability,
        coverage=len(evaluated) / planned if planned else 0,
        perfect_deterministic_score_rate=perfect_score_rate,
        correctness_pass_rate=correctness_pass_rate,
        end_to_end_success_rate=end_to_end_successes / planned if planned else 0,
        coverage_adjusted_deterministic_score=(
            float(coverage_adjusted_score) if coverage_adjusted_score is not None else None
        ),
        successful_generation_rate=len(generated) / planned if planned else 0,
        evaluation_completion_rate=len(evaluated) / len(generated) if generated else 0,
        generation_failure_rate=(
            sum(item.status is BenchmarkSampleStatus.GENERATION_FAILED for item in samples)
            / planned
            if planned
            else 0
        ),
        mean_ai_score=statistics.fmean(ai_scores) if ai_scores else None,
        ai_coverage=len(ai_scores) / len(evaluated) if evaluated else 0,
        mean_judge_score=statistics.fmean(judge_scores) if judge_scores else None,
        mean_adversarial_robustness=statistics.fmean(adversarial) if adversarial else None,
        disputed_rate=(
            sum(item.ai_status == "disputed" for item in evaluated) / len(evaluated)
            if evaluated
            else None
        ),
        ai_unavailable_rate=(
            sum(item.ai_status == "unavailable" for item in evaluated) / len(evaluated)
            if evaluated
            else None
        ),
        generation_costs=dict(sorted(costs.items())),
        mean_generation_latency_ms=(
            statistics.fmean(generation_latencies) if generation_latencies else None
        ),
        median_generation_latency_ms=(
            statistics.median(generation_latencies) if generation_latencies else None
        ),
        p95_generation_latency_ms=percentile_95(generation_latencies),
        generation_latency_distribution_ms=metric_summary(generation_latencies),
        mean_test_execution_seconds=(
            statistics.fmean(test_execution_durations) if test_execution_durations else None
        ),
        median_test_execution_seconds=(
            statistics.median(test_execution_durations) if test_execution_durations else None
        ),
        p95_test_execution_seconds=percentile_95(test_execution_durations),
        test_execution_distribution_seconds=metric_summary(test_execution_durations),
        mean_evaluation_lifecycle_seconds=(
            statistics.fmean(evaluation_lifecycle_durations)
            if evaluation_lifecycle_durations
            else None
        ),
        evaluation_lifecycle_distribution_seconds=metric_summary(evaluation_lifecycle_durations),
        per_task=per_task,
    )


def _task_metrics(task_id: str, samples: list[BenchmarkResultRow]) -> PerTaskMetrics:
    scores = [
        float(item.deterministic_score) for item in samples if item.deterministic_score is not None
    ]
    evaluated = [item for item in samples if item.deterministic_score is not None]
    correctness_passes = sum(item.tests_failed == 0 for item in evaluated)
    end_to_end_successes = sum(
        item.artifact is not None and item.tests_failed == 0 for item in evaluated
    )
    weighted_planned = sum(item.task_weight for item in samples)
    coverage_adjusted = (
        sum(
            Decimal(str(item.deterministic_score)) * Decimal(str(item.task_weight))
            for item in evaluated
            if item.deterministic_score is not None
        )
        / Decimal(str(weighted_planned))
        if weighted_planned > 0
        else None
    )
    planned = len(samples)
    completed = len(evaluated)
    coverage_complete = completed == planned and planned > 0
    consistency: Literal[
        "consistently_correct",
        "sometimes_correct",
        "never_correct",
        "incomplete_coverage",
        "no_completed_evaluations",
    ]
    if completed == 0:
        consistency = "no_completed_evaluations"
    elif correctness_passes == 0:
        consistency = "never_correct"
    elif correctness_passes < completed:
        consistency = "sometimes_correct"
    elif coverage_complete:
        consistency = "consistently_correct"
    else:
        consistency = "incomplete_coverage"
    generation_failures = sum(
        item.status is BenchmarkSampleStatus.GENERATION_FAILED for item in samples
    )
    return PerTaskMetrics(
        task_id=task_id,
        sample_count=planned,
        planned_samples=planned,
        completed_samples=completed,
        coverage=completed / planned if planned else 0,
        generation_failures=generation_failures,
        generation_failure_rate=generation_failures / planned if planned else 0,
        evaluation_failures=sum(
            item.status is BenchmarkSampleStatus.EVALUATION_FAILED for item in samples
        ),
        scores=metric_summary(scores),
        best_score=max(scores) if scores else None,
        worst_score=min(scores) if scores else None,
        perfect_deterministic_score_rate=(
            sum(score == 100 for score in scores) / len(scores) if scores else None
        ),
        correctness_pass_rate=(correctness_passes / len(evaluated) if evaluated else None),
        end_to_end_success_rate=end_to_end_successes / len(samples) if samples else 0,
        coverage_adjusted_deterministic_score=(
            float(coverage_adjusted) if coverage_adjusted is not None else None
        ),
        correctness_consistency=consistency,
        coverage_complete=coverage_complete,
    )


def _stability_label(
    summary: MetricSummary,
) -> Literal["high", "moderate", "low", "not_enough_samples"]:
    deviation = summary.standard_deviation
    if deviation is None:
        return "not_enough_samples"
    if deviation <= 5:
        return "high"
    if deviation <= 15:
        return "moderate"
    return "low"


def _student_t_critical_975(degrees_of_freedom: int) -> float:
    if degrees_of_freedom <= 0:
        raise ValueError("degrees_of_freedom must be positive")
    critical_values = (
        12.7062047364,
        4.30265272975,
        3.18244630528,
        2.7764451052,
        2.57058183564,
        2.44691184879,
        2.36462425101,
        2.3060041352,
        2.26215716285,
        2.22813885196,
        2.20098516008,
        2.17881282966,
        2.16036865646,
        2.14478668792,
        2.13144954556,
        2.11990529922,
        2.10981557783,
        2.10092204024,
        2.09302405441,
        2.08596344727,
        2.07961384473,
        2.0738730679,
        2.06865761042,
        2.06389856163,
        2.05953855275,
        2.05552943864,
        2.05183051648,
        2.0484071418,
        2.04522964213,
        2.0422724563,
    )
    if degrees_of_freedom <= len(critical_values):
        return critical_values[degrees_of_freedom - 1]
    z = 1.959963984540054
    df = float(degrees_of_freedom)
    return (
        z
        + (z**3 + z) / (4 * df)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3)
    )


def _rank_key(entry: LeaderboardEntry) -> tuple[float, float, float, str]:
    return (
        -(entry.weighted_mean_score if entry.weighted_mean_score is not None else -1),
        -entry.coverage,
        -(
            entry.deterministic_scores.median
            if entry.deterministic_scores.median is not None
            else -1
        ),
        entry.model_configuration_fingerprint,
    )
