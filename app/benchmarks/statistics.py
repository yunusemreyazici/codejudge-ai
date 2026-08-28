"""Transparent benchmark aggregations with nulls instead of NaN."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from app.benchmarks.models import (
    BenchmarkModelConfig,
    BenchmarkSampleStatus,
    LeaderboardEntry,
    MetricSummary,
    PerTaskMetrics,
)
from app.benchmarks.repositories import BenchmarkResultRow


def metric_summary(values: list[float]) -> MetricSummary:
    if not values:
        return MetricSummary(count=0)
    return MetricSummary(
        count=len(values),
        mean=statistics.fmean(values),
        median=statistics.median(values),
        standard_deviation=statistics.stdev(values) if len(values) > 1 else None,
        minimum=min(values),
        maximum=max(values),
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
    weighted_denominator = sum(item.task_weight for item in evaluated)
    weighted_mean = (
        sum(
            float(item.deterministic_score) * item.task_weight
            for item in evaluated
            if item.deterministic_score is not None
        )
        / weighted_denominator
        if weighted_denominator > 0
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
    evaluation_latencies = [
        item.evaluation_duration_seconds
        for item in samples
        if item.evaluation_duration_seconds is not None
    ]
    costs: dict[str, Decimal] = defaultdict(Decimal)
    for item in samples:
        if item.generation_cost is not None and item.currency is not None:
            costs[item.currency] += item.generation_cost
    task_groups: dict[str, list[BenchmarkResultRow]] = defaultdict(list)
    for item in samples:
        task_groups[item.task_id].append(item)
    pass_rate = sum(score == 100 for score in scores) / len(scores) if scores else None
    return LeaderboardEntry(
        rank=1,
        model_config_id=config.model_config_id,
        provider_id=config.provider_id,
        model=config.model,
        display_name=config.display_name,
        model_configuration_fingerprint=config.model_configuration_fingerprint,
        weighted_mean_score=weighted_mean,
        deterministic_scores=metric_summary(scores),
        coverage=len(evaluated) / planned if planned else 0,
        pass_rate=pass_rate,
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
        mean_evaluation_latency_seconds=(
            statistics.fmean(evaluation_latencies) if evaluation_latencies else None
        ),
        per_task=[_task_metrics(task_id, task_groups[task_id]) for task_id in sorted(task_groups)],
    )


def _task_metrics(task_id: str, samples: list[BenchmarkResultRow]) -> PerTaskMetrics:
    scores = [
        float(item.deterministic_score) for item in samples if item.deterministic_score is not None
    ]
    return PerTaskMetrics(
        task_id=task_id,
        sample_count=len(samples),
        generation_failures=sum(
            item.status is BenchmarkSampleStatus.GENERATION_FAILED for item in samples
        ),
        evaluation_failures=sum(
            item.status is BenchmarkSampleStatus.EVALUATION_FAILED for item in samples
        ),
        scores=metric_summary(scores),
        best_score=max(scores) if scores else None,
        worst_score=min(scores) if scores else None,
        pass_rate=sum(score == 100 for score in scores) / len(scores) if scores else None,
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
