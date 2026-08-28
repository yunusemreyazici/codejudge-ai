from __future__ import annotations

import pytest

from app.ai.judge import JudgeService
from app.ai.providers.base import ProviderError
from app.ai.scoring import aggregate_judge_scores, calculate_ai_score, calculate_judge_score
from tests.ai.fakes import FakeProvider, judge_output

pytestmark = pytest.mark.ai


async def test_judge_calculates_score_and_normalizes_impossible_line() -> None:
    provider = FakeProvider()
    provider.add("judge", "judge-a", [judge_output(80, line=500)])
    judge = JudgeService(
        provider,
        provider_id="test-provider",
        max_output_tokens=2000,
        temperature=0,
        top_p=1,
    )
    result = await judge.judge(
        model="judge-a",
        payload={"untrusted_candidate_source": "print('hi')"},
        source_line_count=1,
    )
    assert result.score == 80
    assert result.findings[0].line is None
    assert result.source == "llm_judge"
    assert result.raw_response_hash


@pytest.mark.parametrize(
    "mutation",
    [
        {"requirements_adherence": 101},
        {"confidence": 2},
        {"unexpected": "dangerous"},
    ],
)
async def test_strict_invalid_judge_output_is_not_salvaged(mutation: dict[str, object]) -> None:
    provider = FakeProvider()
    output = judge_output()
    output.update(mutation)
    provider.add("judge", "judge-a", [output])
    judge = JudgeService(
        provider,
        provider_id="test-provider",
        max_output_tokens=2000,
        temperature=0,
        top_p=1,
    )
    with pytest.raises(ProviderError, match="malformed_output"):
        await judge.judge(model="judge-a", payload={}, source_line_count=1)


def test_scoring_and_panel_median_are_deterministic() -> None:
    from app.ai.models import JudgeOutput

    assert calculate_judge_score(JudgeOutput.model_validate(judge_output(60))) == 60
    assert aggregate_judge_scores([82, 85, 55]) == (82, 30)
    assert calculate_ai_score(90, 50) == 78


async def test_malformed_judge_json_is_rejected() -> None:
    provider = FakeProvider()
    provider.add("judge", "judge-a", ["not-json"])
    judge = JudgeService(
        provider,
        provider_id="test-provider",
        max_output_tokens=2000,
        temperature=0,
        top_p=1,
    )
    with pytest.raises(ProviderError, match="malformed_output"):
        await judge.judge(model="judge-a", payload={}, source_line_count=1)
