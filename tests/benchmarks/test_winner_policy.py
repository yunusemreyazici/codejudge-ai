from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.benchmarks.winners import (
    INCOMPLETE_EVALUATION_COVERAGE,
    INCOMPLETE_GENERATION_SUCCESS,
    evaluate_winner_eligibility,
    select_winners,
    winner_eligibility_policy_document,
)


@dataclass(frozen=True)
class Candidate:
    model_config_id: UUID
    provider_id: str
    model: str
    display_name: str
    rank: int
    weighted_mean_score: float | None
    coverage: float
    successful_generation_rate: float
    winner_eligible: bool


def _candidate(
    name: str,
    rank: int,
    score: float | None,
    *,
    eligible: bool,
    coverage: float = 1,
) -> Candidate:
    return Candidate(
        model_config_id=uuid4(),
        provider_id="fake",
        model=name,
        display_name=name,
        rank=rank,
        weighted_mean_score=score,
        coverage=coverage,
        successful_generation_rate=coverage,
        winner_eligible=eligible,
    )


@pytest.mark.parametrize(
    ("planned", "generated", "evaluated", "eligible", "reasons"),
    [
        (
            7,
            1,
            1,
            False,
            (INCOMPLETE_GENERATION_SUCCESS, INCOMPLETE_EVALUATION_COVERAGE),
        ),
        (7, 7, 7, True, ()),
        (7, 7, 6, False, (INCOMPLETE_EVALUATION_COVERAGE,)),
        (7, 6, 7, False, (INCOMPLETE_GENERATION_SUCCESS,)),
        (
            21,
            20,
            20,
            False,
            (INCOMPLETE_GENERATION_SUCCESS, INCOMPLETE_EVALUATION_COVERAGE),
        ),
        (21, 21, 21, True, ()),
    ],
)
def test_winner_eligibility_uses_all_planned_integer_counts(
    planned: int,
    generated: int,
    evaluated: int,
    eligible: bool,
    reasons: tuple[str, ...],
) -> None:
    result = evaluate_winner_eligibility(
        planned_generations=planned,
        successful_generations=generated,
        completed_evaluations=evaluated,
    )

    assert result.eligible is eligible
    assert result.reasons == reasons


def test_policy_is_about_completeness_not_correctness() -> None:
    # Correctness is intentionally absent from the centralized policy inputs.
    result = evaluate_winner_eligibility(
        planned_generations=7,
        successful_generations=7,
        completed_evaluations=7,
    )

    assert result.eligible is True
    assert "correct" not in str(winner_eligibility_policy_document()).lower()


def test_high_partial_score_is_observed_only_and_complete_lower_score_is_eligible() -> None:
    high_partial = _candidate("high-partial", 1, 99.2, eligible=False, coverage=1 / 7)
    complete = _candidate("complete", 2, 90.49, eligible=True)

    winners = select_winners([high_partial, complete], final=True)

    assert winners.observed is high_partial
    assert winners.eligible is complete


def test_no_eligible_model_never_falls_back_to_observed_winner() -> None:
    observed = _candidate("partial", 1, 99, eligible=False, coverage=0.5)

    winners = select_winners([observed], final=True)

    assert winners.observed is observed
    assert winners.eligible is None


def test_multiple_eligible_models_and_exact_tie_reuse_existing_order() -> None:
    first = _candidate("stable-first", 1, 88, eligible=True)
    second = _candidate("stable-second", 2, 88, eligible=True)

    winners = select_winners([first, second], final=True)

    assert winners.observed is first
    assert winners.eligible is first


def test_models_without_completed_evaluations_are_not_observed_winners() -> None:
    unavailable = _candidate("unavailable", 1, None, eligible=False, coverage=0)
    measured = _candidate("measured", 2, 10, eligible=False, coverage=1 / 7)

    winners = select_winners([unavailable, measured], final=True)

    assert winners.observed is measured


def test_running_winners_are_suppressed_even_when_samples_exist() -> None:
    candidate = _candidate("early-result", 1, 100, eligible=True)

    winners = select_winners([candidate], final=False)

    assert winners.final is False
    assert winners.observed is None
    assert winners.eligible is None
