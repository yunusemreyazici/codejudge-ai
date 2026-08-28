"""CodeJudge-owned deterministic aggregation of structured AI signals."""

from __future__ import annotations

from statistics import median

from app.ai.models import JudgeOutput


def calculate_judge_score(output: JudgeOutput) -> float:
    score = (
        output.requirements_adherence * 0.30
        + (100 - output.logic_risk) * 0.25
        + output.maintainability * 0.25
        + output.edge_case_coverage * 0.20
    )
    return round(min(100.0, max(0.0, score)), 2)


def aggregate_judge_scores(scores: list[float]) -> tuple[float, float]:
    if not scores:
        raise ValueError("At least one judge score is required")
    return round(float(median(scores)), 2), round(max(scores) - min(scores), 2)


def calculate_ai_score(judge_score: float, adversarial_robustness: float) -> float:
    return round(min(100.0, max(0.0, judge_score * 0.70 + adversarial_robustness * 0.30)), 2)
