# Benchmark methodology

[← Project README](../../README.md) · [Documentation index](../README.md)

A CodeJudge benchmark expands an immutable dataset across ordered model configurations and one or
more independent samples per model/task pair. Every accepted generation enters the ordinary
CodeJudge evaluator; benchmark code does not bypass sandbox, correctness, analyzer, scoring, or
snapshot integrity.

## Controlled flow

1. Load a schema-versioned YAML configuration.
2. Resolve the immutable dataset and public coding prompt.
3. Validate model identities, provider capabilities, limits, pricing, and duplicate identities.
4. Expand `models × tasks × samples_per_task` and calculate a conservative maximum cost.
5. Reject unknown or over-budget paid execution before any provider request.
6. Durably queue a run only through the explicit `run` command or benchmark API.
7. Generate candidate source under provider-level concurrency and timeout controls.
8. Reject blank generation before artifact persistence or evaluation.
9. Persist valid source, hashes, token/cost evidence, and provider provenance.
10. Evaluate source with the ordinary Docker, analyzer, scoring, and snapshot pipeline.
11. Aggregate immutable results without rerunning providers or evaluations.

`plan` is safe and provider-free. `probe` makes exactly one sanitized diagnostic request with
retries disabled. `run` is the provider-execution boundary and is intentionally noninteractive
after it prints the full plan.

## Primary leaderboard

Models are ordered by:

1. higher task-weighted mean deterministic score over completed evaluations;
2. higher evaluation coverage;
3. higher deterministic median;
4. stable model-configuration fingerprint.

Repeated samples are averaged within each model/task pair before the dataset task weight is applied
once. This prevents a task with more completed repeats from receiving accidental extra weight.

## Observed leader and eligible winner

The observed leader is the highest primary score among models with any completed observations.
The eligible winner must also have:

- 100% generation success over planned samples; and
- 100% evaluation coverage over planned samples.

If no model meets both conditions, there is no eligible winner. Eligibility does not alter primary
scores or ordering; it distinguishes complete evidence from a potentially impressive sparse
observation.

## Coverage and reliability

- **Generation success** = valid generated sources / planned samples.
- **Evaluation coverage** = completed evaluations / planned samples.
- **Evaluation completion** = completed evaluations / successful generations.
- **Correctness pass rate** = authoritative correct evaluations / completed evaluations.
- **End-to-end success** = planned samples that generated, evaluated, and passed official tests /
  planned samples.
- **Coverage-adjusted score** = task-weighted planned-sample metric that assigns zero to missing
  evaluations.

Primary score excludes missing evaluations rather than fabricating zeros. The explicitly named
coverage-adjusted score supplies the complementary reliability penalty. Both must be reported.

## Timing and cost

Generation request latency, sandbox official-test time, and total evaluation lifecycle duration
are separate measurements. Lifecycle duration can include queueing and generation and must not be
presented as code execution time.

Pricing is a provider/model/version snapshot. Actual tokens and derived cost are stored with each
artifact. Unknown pricing is null, never free. Cost-per-success metrics appear only when the
denominator is nonzero and generation-cost coverage is complete.

## Interpretation limits

Results are conditional on the dataset, public prompt, official tests, model parameters, sampling,
provider backend/version, rate limits, response compatibility, and execution-time CodeJudge
identity. Small panels and one-sample task cells do not establish broad statistical superiority.
Optional AI assessment is supplemental and never affects deterministic rank or winner eligibility.

Continue with [Datasets](datasets.md), [Statistics](statistics.md), [Providers](providers.md), and
[Historical results](results.md).
