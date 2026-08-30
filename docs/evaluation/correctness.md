# Correctness semantics

[← Project README](../../README.md) · [Documentation index](../README.md)

Official tests are the authority for deterministic correctness. A model's explanation, static
quality, composite score, or generated adversarial tests cannot turn a failing official evaluation
into a correctness pass.

## Official-test contract

Every repository task identifies a versioned official test suite and expected test count. The
runner returns structured `passed`, `failed`, and `total` counts. CodeJudge checks that:

- counts are nonnegative;
- `passed + failed == total`;
- the report is structurally valid;
- benchmark evaluations have zero failed official tests to count as correct;
- the official test total exactly matches the task's expected count when that count is known.

For an explicitly extensible task without a known exact count, the reported total must still be
positive. Zero executed tests never qualifies as correct.

## Why test-count integrity matters

A process may exit successfully without collecting the intended suite, or a malformed report may
contain plausible values. Checking only `tests_failed == 0` would treat “no tests ran” as success.
CodeJudge therefore couples failure count with the authoritative test-count contract.

This invariant is enforced at evaluation and benchmark reporting boundaries. It protects:

- correctness pass rate;
- end-to-end success rate;
- eligible-winner reporting;
- persisted historical results.

## Failure classes

Candidate-caused outcomes are represented as completed deterministic evidence:

- assertion/test failures;
- syntax and import failures surfaced by the official suite;
- explicit timeout;
- authoritative container OOM;
- bounded-output violations and ordinary execution findings.

Infrastructure inability to produce trustworthy evidence is different. Invalid structured reports,
runner failures, analyzer failures, or runtime identity mismatches fail closed and are not silently
converted into a candidate score.

## Hidden-test privacy

Candidate workspaces contain only the submitted `solution.py`. Official assertions, expected
values, future operations, task references, and evaluator paths stay on the trusted side of the
protocol. A host-side harness sends one bounded public-API operation at a time to a stateful
candidate process.

The coding provider receives only the public task prompt. AI judges receive the public task,
candidate source marked as untrusted data, and structured deterministic evidence—not hidden tests
or reference implementations.

## Related metrics

Correctness score is the percentage of official tests passed within one valid evaluation.
Benchmark correctness pass rate is the share of completed evaluations that meet the full
authoritative pass invariant. Perfect deterministic score rate means exactly `score == 100` and is
reported separately; it must not be used as a synonym for test correctness.

Read [Scoring](scoring.md) for the composite policy and [Datasets](../benchmarks/datasets.md) for
task/test identity.
