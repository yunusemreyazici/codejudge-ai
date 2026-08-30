# Deterministic scoring

[← Project README](../../README.md) · [Documentation index](../README.md)

CodeJudge's top-level score is deterministic. It is computed from official-test evidence and local
analyzer results under an explicit scoring-policy version. Provider or AI judge output cannot
change it.

## Policy version 1

When all static-analysis dimensions are available, the weighted score is:

| Dimension | Weight | Evidence source |
| --- | ---: | --- |
| Correctness | 0.60 | Official tests executed by the configured runner |
| Code quality | 0.15 | Ruff findings |
| Type safety | 0.10 | mypy findings |
| Security | 0.10 | Bandit findings |
| Complexity | 0.05 | Radon complexity analysis |

The implementation computes the weighted sum and rounds to two decimal places. Each dimension is
bounded from 0 to 100. The weights sum to 1.0.

If static analysis is intentionally disabled, every static dimension is absent and the final score
equals correctness. A mixed state—some static dimensions present and some missing—is invalid.
Static analyzer infrastructure failure fails closed instead of creating a misleading partial score.

## Correctness score

For a valid official-test report with at least one test:

```text
correctness = passed tests / total tests × 100
```

When zero tests execute, correctness is `0` and the result includes an explicit finding that
correctness could not be measured. Benchmark correctness applies additional authoritative
test-count integrity; see [Correctness](correctness.md).

## Static dimensions

Static dimensions begin at 100 and apply deterministic deductions from normalized analyzer
findings. Deductions and severity handling belong to the versioned scoring policy, not to routes,
workers, benchmark aggregation, or an external model.

| Dimension | Deterministic policy |
| --- | --- |
| Code quality | Deduct 10/5/2 per error/warning/info finding |
| Type safety | Deduct 8/4/0 per error/warning/info finding; annotations are not required |
| Security | Deduct 25/10/3 by severity, multiplied by 0.50/0.75/1.00 for low/medium/high confidence |
| Complexity | Maximum cyclomatic complexity 1–5 → 100, 6–10 → 90, 11–15 → 70, 16–20 → 50, above 20 → 25; unanalyzable source → 0 |

Every dimension is clamped to 0–100. Analyzer versions are captured in the immutable evaluation
snapshot.

## AI separation

Optional AI assessment is stored in a separate `ai_assessment` structure. It may include judge
scores, adversarial robustness, coverage, disagreements, model and prompt identities, or an
unavailable/partial status. None of these fields is blended into:

- the top-level deterministic score;
- any deterministic score-breakdown dimension;
- benchmark primary rank;
- benchmark winner eligibility.

This separation lets an evaluation remain valid when a provider is unavailable and keeps
historical deterministic results comparable without contacting a model again.

When both optional AI components are valid, AI policy version `1` computes
`judge_score × 0.70 + adversarial_robustness × 0.30`. Missing or disputed components produce no
aggregate AI score; weights are not renormalized. This formula remains outside deterministic
scoring.

## Benchmark interpretation

The benchmark primary score is a task-weighted mean over completed deterministic evaluations. It
is not the same as:

- correctness pass rate;
- perfect deterministic score rate;
- coverage-adjusted score;
- end-to-end success rate;
- optional AI score.

Always read primary quality with generation success and evaluation coverage. See
[Methodology](../benchmarks/methodology.md) and [Statistics](../benchmarks/statistics.md).
