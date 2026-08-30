# Repeated-sample statistics

[← Project README](../../README.md) · [Documentation index](../README.md)

Repeated samples expose hosted-model variation without changing CodeJudge's primary ranking rule.
Statistics are deterministic functions of persisted observations.

## Task-first aggregation

For each model, CodeJudge first computes the arithmetic mean of completed scores inside each task.
It then applies the dataset weight to each task mean exactly once. This avoids overweighting a task
because it happened to produce more completed repeats.

Missing planned repeats do not enter the observed primary mean. They contribute zero only to the
supplemental coverage-adjusted score. Coverage is always displayed beside quality.

## Distribution fields

Reports include, where meaningful:

- arithmetic mean and median;
- minimum and maximum;
- sample standard deviation using the `n - 1` convention;
- deterministic two-sided 95% Student-t confidence interval for the observed arithmetic mean;
- per-task score and correctness distributions;
- generation and evaluation reliability denominators.

Fewer than two observations produce unknown deviation and confidence interval. A one-sample-per-task
run also leaves model repeated-sample deviation and confidence unknown: variation between different
tasks is not mislabeled as repeat stability.

The interval summarizes observed repeated samples under the recorded conditions. It does not claim
uncertainty about future provider versions or a universal population of coding tasks.

## Stability label

When enough repeated observations exist, score deviation is summarized as:

| Sample standard deviation | Label |
| ---: | --- |
| ≤ 5 | High stability |
| > 5 and ≤ 15 | Moderate stability |
| > 15 | Low stability |
| Insufficient repetition | Not enough samples |

Stability is supplemental. It does not alter primary rank or winner eligibility.

## Example from preserved evidence

The completed two-model 3× run planned 42 generations over seven tasks. Kimi K2.7 Code recorded a
mean of 88.14, sample deviation 12.69, and 95% interval `[82.36, 93.91]`. DeepSeek V4 Pro recorded a
mean of 75.71, deviation 26.74, and interval `[63.54, 87.88]`. Both had 100% generation success and
evaluation coverage.

These figures describe that run, dataset, prompt, provider state, and CodeJudge 0.7.7 runtime. They
do not establish that the confidence intervals predict another provider deployment.
