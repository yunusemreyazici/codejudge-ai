# Historical benchmark results

[← Project README](../../README.md) · [Documentation index](../README.md)

This page presents the strongest preserved completed real-model panels plus one partial diagnostic
retest in the local CodeJudge history. Figures come from immutable benchmark rows and evaluation
snapshots; no provider was called to prepare this documentation.

All documented runs used `codejudge-core@2` with dataset fingerprint:

```text
ee0f631d6810c039e84d90d9f2b77f20dcabbe27bef0af600695ab9cb1111988
```

The runtime column below is the **execution-time CodeJudge version stored on evaluations**, not the
current project version and not a later archive-creation version.

## Run identity

| Panel | Run ID | Samples/task | Runtime |
| --- | --- | ---: | --- |
| Original smoke | `b70d025e-54a2-4380-85a5-e33b367600f5` | 1 | 0.7.5 |
| Repeated 3× | `dfa19d21-f3d7-4c93-8471-05b690ba64fe` | 3 | 0.7.7 |
| ClinePass all-model | `2737e6cc-a11e-4356-ba1e-fbe33ffd1d9c` | 1 | 0.7.8 |
| OpenRouter free | `d99b6150-a6b6-4b2a-9997-ef370ea31640` | 1 | 0.7.8 |
| OpenRouter paid | `95f5d0c1-3799-47fa-80ab-b56196a15206` | 1 | 0.7.8 |
| OpenRouter diagnostic retest | `1bd95729-d988-474b-96cb-0fef9d389513` | 1 | 0.7.10 |

## Overall run reliability

Every value in this table is aggregated across all configured models in the named run. It does not
describe the eligible winner model by itself.

| Panel | Planned | Generated | Completed | Overall generation success | Overall evaluation coverage | Gen failures | Eval failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Original smoke | 14 | 14 | 14 | 100.0% | 100.0% | 0 | 0 |
| Repeated 3× | 42 | 42 | 42 | 100.0% | 100.0% | 0 | 0 |
| ClinePass all-model | 84 | 73 | 73 | 86.9% | 86.9% | 11 | 0 |
| OpenRouter free | 77 | 25 | 22 | 32.5% | 28.6% | 52 | 3 |
| OpenRouter paid | 119 | 72 | 72 | 60.5% | 60.5% | 47 | 0 |
| OpenRouter diagnostic retest | 70 | 25 | 23 | 35.7% | 32.9% | 43 | 2 |

An observed winner may have incomplete evidence. The eligible winner is the best-ranked model with
100% generation success and 100% evaluation coverage across all planned samples.

## Original ClinePass two-model smoke

- Completed 2026-08-29 at 05:12 UTC.
- Run fingerprint:
  `806c319a6c3fcd9bd502ca8f0516697619df83b57e3adffdf60d8ec500b17381`.
- Both models generated and evaluated all seven tasks.

| Model | Eligible | Mean | Correctness pass rate | End-to-end | Cost/planned |
| --- | --- | ---: | ---: | ---: | ---: |
| Kimi K2.7 Code | yes | 84.54 | 28.6% | 28.6% | USD 0.001707207143 |
| DeepSeek V4 Pro | yes | 82.15 | 14.3% | 14.3% | USD 0.005824525714 |

Observed and eligible winner: **Kimi K2.7 Code, 84.54**. With one observation per task, repeated-
sample deviation and confidence intervals are correctly reported as unavailable.

## Repeated two-model panel

- Completed 2026-08-29 at 07:24 UTC.
- Run fingerprint:
  `d1031728b478c3c29ca0fc361944eb3f0978c4beb24462186858247c23cc0389`.
- Three independent samples × seven tasks × two models produced 42/42 completed evaluations.

| Model | Eligible | Mean | 95% observed interval | Sample std dev | Correctness | Cost/planned |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| Kimi K2.7 Code | yes | 88.14 | [82.36, 93.91] | 12.69 | 33.3% | USD 0.001619288095 |
| DeepSeek V4 Pro | yes | 75.71 | [63.54, 87.88] | 26.74 | 28.6% | USD 0.007143114286 |

Observed and eligible winner: **Kimi K2.7 Code, 88.14**. Both models had 21/21 generation success
and evaluation coverage, so the winner comparison is not driven by missing samples. The intervals
describe stored observations under this run's conditions; they do not predict a future provider
deployment.

## ClinePass 12-model panel

- Completed 2026-08-29 at 10:44 UTC.
- Run fingerprint:
  `fa7c8784b507e8726df590d1750712f4dfa474a2f0613ab5a6a739205c660d2e`.
- Twelve models × seven tasks planned 84 generations; 73 completed.

The observed leader was **Qwen3.8 Max, 99.20**, but it generated only 1/7 samples and was not
eligible. The eligible winner was **GLM-5.2, 90.49**, with 7/7 generation and evaluation coverage,
a 42.9% correctness pass rate, and recorded cost per planned sample of USD 0.002201285714.

| Reliability exception | Generated | Failure evidence |
| --- | ---: | --- |
| Kimi K3 | 6/7 | `provider_unavailable=1` |
| Kimi K2.6 | 3/7 | `provider_unavailable=4` |
| Qwen3.8 Max | 1/7 | `provider_unavailable=6` |

The other nine models generated and evaluated 7/7 samples. This panel demonstrates why the
observed leader and eligible winner must be reported separately.

## OpenRouter free panel

- Completed 2026-08-29 at 11:13 UTC.
- Run fingerprint:
  `7165553b6f55b2903744957a731a96ec727dadd7ae61a96259e40e5c65d9689e`.
- Eleven models × seven tasks planned 77 generations; only 22 evaluations completed.

The observed leader was **Dots3 Note Preview Free, 93.87** on 2/7 generated samples. The only
eligible winner was **Nemotron 3 Super Free, 80.14**, with 7/7 generation and evaluation coverage.
Its recorded price was zero under the preserved pricing snapshot; that is different from an unknown
price.

Reliability dominates interpretation: the run recorded 52 generation failures and three evaluation
failures. Normalized generation failures included `provider_error`, `rate_limited`, and
`invalid_response`. Four models generated no accepted candidate; several high observed means came
from two to four samples. This is evidence about that provider/model interaction, not a complete
quality ranking of the free catalog.

## OpenRouter paid panel

- Completed 2026-08-29 at 12:06 UTC.
- Run fingerprint:
  `06cbd3b0cbd95b53644b2bb4b829353c38e61c1ad82b3d81d244b2d06a74a9b6`.
- Seventeen models × seven tasks planned 119 generations; 72 evaluations completed.

The observed leader was **Qwen3.8 Flash, 99.20** on 1/7 samples. The eligible winner was
**GPT-5.6 Luna, 92.87**, with 7/7 generation and evaluation coverage, 42.9% correctness, and
recorded cost per planned sample of USD 0.001714571429. Gemini 3.7 Flash was also fully eligible at
92.75 with 57.1% correctness and recorded cost per planned sample of USD 0.003901553571.

Seven models had complete 7/7 generation coverage. Ten were incomplete; the run recorded 47
generation failures, all normalized as `invalid_response` in the preserved snapshot. There were no
evaluation failures. Again, incomplete high observations were not allowed to become the eligible
winner.

## OpenRouter invalid-response diagnostic retest

- Completed in `partial` status on 2026-08-29 at 17:34 UTC.
- Run fingerprint:
  `a4236ffe40213914de459fa6786c1c99daae91d4539a21c499209bcfa2f7496a`.
- Ten previously unreliable paid routes × seven tasks planned 70 generations.
- Execution-time CodeJudge version was 0.7.10.

The retest completed 23 evaluations (32.9% coverage), with 43 generation failures and two
evaluation failures. It had **no eligible winner**. Qwen3.8 Max was the observed leader at 99.20
from only 1/7 samples and therefore remained ineligible. DeepSeek V4 Flash 0731 had the strongest
generation coverage in the panel at 6/7; the other routes generated one to three accepted samples.

Every recorded generation failure was normalized as `invalid_response` in the current derived
report. This run is retained as diagnostic evidence about response compatibility and reliability,
not as a completed leaderboard or a claim about overall model quality.

## How to interpret these panels

- Compare quality and coverage together; do not rank only the largest observed mean.
- Treat the eligible winner as the complete-evidence winner under the recorded policy, not a claim
  of universal model superiority.
- One sample per task cannot estimate repeat stability. The 3× panel is the appropriate preserved
  source for dispersion and Student-t interval fields.
- Correctness pass rate means authoritative official-test completion, not `score == 100`.
- Prices are immutable run snapshots. Unknown cost remains unknown; a recorded zero is explicit.
- Provider availability, backend changes, rate limits, prompt/config parameters, and the
  execution-time CodeJudge version can all affect results.
- Optional AI fields do not affect any winner shown here.

Use `codejudge-benchmark show <RUN_ID>` for a provider-free database rendering. Archive layout,
review, and publication rules are documented in the
[benchmark-results policy](../../benchmark-results/README.md) and
[Reproducibility](reproducibility.md).
