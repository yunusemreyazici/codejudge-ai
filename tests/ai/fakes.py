from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Iterable

from app.ai.models import ProviderResponse, ProviderUsage, StructuredLLMRequest
from app.ai.providers.base import ProviderError
from app.evaluator.models import RunnerResult


class FakeProvider:
    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], deque[str | ProviderError]] = defaultdict(deque)
        self.requests: list[StructuredLLMRequest] = []
        self.closed = False

    def add(self, component: str, model: str, outputs: Iterable[object]) -> None:
        for output in outputs:
            if isinstance(output, ProviderError):
                self.responses[(component, model)].append(output)
            elif isinstance(output, str):
                self.responses[(component, model)].append(output)
            else:
                self.responses[(component, model)].append(json.dumps(output))

    async def complete_structured(self, request: StructuredLLMRequest) -> ProviderResponse:
        self.requests.append(request)
        output = self.responses[(request.component, request.model)].popleft()
        if isinstance(output, ProviderError):
            raise output
        return ProviderResponse(
            content=output,
            response_id=f"fake-{len(self.requests)}",
            usage=ProviderUsage(input_tokens=100, output_tokens=50),
            latency_ms=7,
        )

    async def close(self) -> None:
        self.closed = True


class FakeSandbox:
    def __init__(
        self,
        *,
        reference_passes: bool = True,
        candidate_passes: bool = False,
        reference_timed_out: bool = False,
    ) -> None:
        self.reference_passes = reference_passes
        self.candidate_passes = candidate_passes
        self.reference_timed_out = reference_timed_out
        self.calls: list[tuple[str, str]] = []

    async def run(
        self,
        *,
        solution_source: str,
        test_source: str,
        task_id: str,
        timeout_seconds: float,
    ) -> RunnerResult:
        del task_id, timeout_seconds
        self.calls.append((solution_source, test_source))
        is_reference = "Trusted LRU cache oracle" in solution_source
        if is_reference and self.reference_timed_out:
            return RunnerResult(
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=1,
                passed=0,
                failed=0,
                total=0,
                timed_out=True,
            )
        passes = self.reference_passes if is_reference else self.candidate_passes
        return RunnerResult(
            exit_code=0 if passes else 1,
            stdout="",
            stderr="",
            duration_seconds=0.01,
            passed=int(passes),
            failed=int(not passes),
            total=1,
        )


def judge_output(score: float = 80, *, line: int | None = None) -> dict[str, object]:
    return {
        "requirements_adherence": score,
        "logic_risk": 100 - score,
        "maintainability": score,
        "edge_case_coverage": score,
        "confidence": 0.8,
        "findings": [
            {
                "severity": "warning",
                "category": "logic",
                "message": "Potential edge case.",
                "line": line,
            }
        ],
        "summary": "Structured fake assessment.",
    }


def generated_output() -> dict[str, object]:
    return {
        "tests": [
            {
                "name": "test_repeated_update",
                "rationale": "Exercise recency updates.",
                "code": (
                    "from solution import LRUCache\n\n"
                    "def test_repeated_update():\n"
                    "    cache = LRUCache(1)\n"
                    "    cache.put(1, 1)\n"
                    "    cache.put(1, 2)\n"
                    "    assert cache.get(1) == 2\n"
                ),
            }
        ]
    }
