"""Centralized benchmark winner eligibility and selection policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

INCOMPLETE_GENERATION_SUCCESS = "incomplete_generation_success"
INCOMPLETE_EVALUATION_COVERAGE = "incomplete_evaluation_coverage"
WINNER_INELIGIBILITY_REASON_ORDER = (
    INCOMPLETE_GENERATION_SUCCESS,
    INCOMPLETE_EVALUATION_COVERAGE,
)
WINNER_ELIGIBILITY_POLICY_ID = "complete_generation_and_evaluation"
WINNER_ELIGIBILITY_POLICY_DESCRIPTION = (
    "100% generation success and 100% evaluation coverage across all planned samples."
)


@dataclass(frozen=True, slots=True)
class WinnerEligibility:
    eligible: bool
    reasons: tuple[str, ...]


class WinnerCandidate(Protocol):
    model_config_id: Any
    provider_id: str
    model: str
    display_name: str
    rank: int
    weighted_mean_score: float | None
    coverage: float
    successful_generation_rate: float
    winner_eligible: bool


@dataclass(frozen=True, slots=True)
class WinnerSelection:
    observed: WinnerCandidate | None
    eligible: WinnerCandidate | None
    final: bool


def evaluate_winner_eligibility(
    *,
    planned_generations: int,
    successful_generations: int,
    completed_evaluations: int,
) -> WinnerEligibility:
    """Evaluate completeness using authoritative integer counts, never rounded rates."""
    generation_complete = planned_generations > 0 and successful_generations == planned_generations
    evaluation_complete = planned_generations > 0 and completed_evaluations == planned_generations
    reasons = tuple(
        reason
        for reason, satisfied in (
            (INCOMPLETE_GENERATION_SUCCESS, generation_complete),
            (INCOMPLETE_EVALUATION_COVERAGE, evaluation_complete),
        )
        if not satisfied
    )
    return WinnerEligibility(eligible=not reasons, reasons=reasons)


def winner_eligibility_policy_document() -> dict[str, Any]:
    return {
        "id": WINNER_ELIGIBILITY_POLICY_ID,
        "description": WINNER_ELIGIBILITY_POLICY_DESCRIPTION,
        "generation_requirement": "successful_generations == planned_generations",
        "evaluation_requirement": "completed_evaluations == planned_generations",
        "uses_integer_counts": True,
        "ineligibility_reason_order": list(WINNER_INELIGIBILITY_REASON_ORDER),
    }


def select_winners(
    ordered_leaderboard: Sequence[WinnerCandidate], *, final: bool
) -> WinnerSelection:
    """Select from the existing deterministic leaderboard without changing its order."""
    if not final:
        return WinnerSelection(observed=None, eligible=None, final=False)
    observed = next(
        (entry for entry in ordered_leaderboard if entry.weighted_mean_score is not None),
        None,
    )
    eligible = next(
        (
            entry
            for entry in ordered_leaderboard
            if entry.weighted_mean_score is not None and entry.winner_eligible
        ),
        None,
    )
    return WinnerSelection(observed=observed, eligible=eligible, final=True)


def winner_reference(candidate: WinnerCandidate | None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "model_config_id": candidate.model_config_id,
        "provider_id": candidate.provider_id,
        "model": candidate.model,
        "display_name": candidate.display_name,
        "rank": candidate.rank,
        "primary_mean": candidate.weighted_mean_score,
        "generation_success_rate": candidate.successful_generation_rate,
        "evaluation_coverage": candidate.coverage,
    }


def eligibility_from_model_document(model: Mapping[str, Any]) -> WinnerEligibility:
    """Derive eligibility for current and historical schema-v2 model documents."""
    planned = int(model.get("planned_samples", 0))
    return evaluate_winner_eligibility(
        planned_generations=planned,
        successful_generations=int(model.get("successful_generations", 0)),
        completed_evaluations=int(model.get("completed_evaluations", 0)),
    )
