# Providers, output modes, and budgets

[← Project README](../../README.md) · [Documentation index](../README.md)

CodeJudge's real-generation adapter supports OpenAI-compatible chat completions. Provider
configuration is explicit and commit-safe: YAML stores logical identities and environment-variable
names, never endpoint values or credentials.

## Provider configuration

```yaml
providers:
  provider-a:
    protocol: openai-compatible
    base_url_env: CODEJUDGE_PROVIDER_A_BASE_URL
    credential_env: CODEJUDGE_PROVIDER_A_API_KEY
    output_mode: structured_json
    request_timeout_seconds: 30
    max_concurrent_requests: 1
```

Use placeholders in committed examples. Export the named variables only in the execution
environment, never in YAML, logs, reports, or issues.

## Output modes

`structured_json` requests a strict JSON Schema response and locally decodes the candidate field.
`raw_source` omits `response_format` and treats nonblank assistant message content as exact source,
without fence removal, prose cleanup, or newline normalization.

The adapter accepts ordinary completion envelopes with root `choices` and compatible gateway
wrappers whose completion is under `data`. Unknown gateway metadata is ignored unless CodeJudge
explicitly uses it. Provider-envelope errors and candidate-output errors remain distinct normalized
failure categories.

Empty or whitespace-only source fails before generation-artifact persistence. Evaluation is not
invoked. Valid source whitespace and indentation are preserved byte-for-byte.

## Concurrency and timeout

`max_concurrent_requests` is a per-provider, per-worker-process semaphore shared by every model
using that provider configuration. Adding models does not create independent concurrency pools for
the same provider. When omitted, benchmark worker concurrency remains the limiting policy.

Provider request timeout is explicit and bounded. Output mode, timeout, and provider concurrency
are persisted in model provenance and change configuration fingerprints. Directly compared models
should normally use the same output mode; reports disclose mixed modes.

## Planning and cost boundary

Planning expands all models, tasks, and samples before execution. The current defensive ceilings
allow up to 20 models, 10 tasks, 10 samples per task, and 500 total generations. A 12-model,
seven-task run plans 84 generations at one sample or 252 at three samples.

Each model has a reviewed pricing snapshot:

```yaml
pricing:
  provider-a/model-a:
    version: reviewed-YYYY-MM-DD
    currency: USD
    input_per_million: 1.00
    output_per_million: 4.00

max_generation_cost:
  amount: 10.00
  currency: USD
```

The conservative preflight uses bounded input plus configured maximum output tokens across every
planned generation. Paid execution is rejected before provider contact when known maximum cost
exceeds the cap, any required price is unknown, or mixed currencies would require conversion.
The estimate is not actual spend; actual provider-reported tokens and snapshot-derived cost are
stored after generation.

## Failure reporting

Reports normalize persisted generation failures into stable public categories: `rate_limited`,
`unauthorized`, `forbidden`, `not_found`, `provider_unavailable`, `provider_timeout`,
`provider_error`, `invalid_response`, `malformed_output`, and `unknown`. Provider-envelope shape
failures map to `invalid_response`; invalid structured candidate output maps to `malformed_output`.

An optional composite detail is stored only when it is an allowlisted bounded token, such as
`missing_choices`, `reasoning_only`, `tool_call_only`, or `empty_output`. Raw provider output,
exception text, URLs, and secrets cannot enter that field. Historical base-only codes remain valid
and render with `unknown_detail` when no detail was recorded.

## Safe workflow

```bash
cp benchmark-configs/real-smoke.example.yaml benchmark-configs/real-smoke.yaml
uv run codejudge-benchmark plan benchmark-configs/real-smoke.yaml
uv run codejudge-benchmark probe benchmark-configs/real-smoke.yaml --model model-a
uv run codejudge-benchmark run benchmark-configs/real-smoke.yaml
```

`probe` makes exactly one request, disables retries, and hides content by default. Add
`--show-content` only for deliberate local review. `run` prints the full plan and then durably
queues the run; it returns immediately unless `--wait` is supplied.

Tests and CI use fake providers and never contact real APIs.
