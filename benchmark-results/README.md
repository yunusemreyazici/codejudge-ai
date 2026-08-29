# Benchmark result artifacts

This directory is the local, review-before-publication home for benchmark exports, archives, and
comparisons. CodeJudge writes files only when explicitly asked. It never commits, pushes, publishes,
registers an archive in PostgreSQL, or edits a release automatically.

## Layout

```text
benchmark-results/
  README.md
  generated/<run-id>/
    results.json
    report.md
    candidates/*.py
  runs/<run-id>/
    manifest.json
    results.json
    report.md
    candidates/*.py
  comparisons/<run-a>_vs_<run-b>/
    comparison.json
    comparison.md
  published/<reviewed-name>/
    ...
```

`generated/` remains Git-ignored scratch output for `export` and `report`. `runs/` is the default
destination for complete immutable archives. `comparisons/` is the recommended destination for
portable JSON and GitHub-renderable Markdown comparisons. Nothing under `runs/`, `comparisons/`,
or `published/` is automatically added to Git.

## Reading a run

Current run exports retain Phase 7.4 results schema version `2`. Each run contains immutable run,
dataset, task/test, prompt, model configuration, evaluator, and generated-source fingerprints plus
the persisted samples and metric inputs needed for audit.

The primary weighted deterministic mean measures quality over completed evaluations only and must
always be read with coverage. The coverage-adjusted deterministic score is supplemental: it retains
task weights and assigns zero only to missing planned evaluations. Correctness pass is the share of
completed evaluations with zero failed official tests. End-to-end success is the share of all
planned samples that generated, completed evaluation, and passed correctness. Successful generation
and generation-failure rates describe provider reliability separately from evaluated code quality.

Reports keep provider generation latency, sandbox correctness-test execution, and the longer
sample-to-snapshot lifecycle separate. Cost-per-success is unknown or not applicable when its
denominator is zero or cost coverage is incomplete—never zero by assumption.

Repeated runs retain every independently identified model/task/sample-index observation. Primary
quality is calculated task-first: completed repeats are averaged within a task, then each dataset
task weight is applied once. Missing repeats affect only coverage and the supplemental
coverage-adjusted score. Score and latency deviations use sample standard deviation (`n-1`), and
the 95% mean interval uses Student's t distribution; both are unknown when fewer than two relevant
observations exist. Model repeat uncertainty also stays unknown for one-sample-per-task runs,
because cross-task variation is not repeat stability. These descriptive intervals characterize the
archived observations, not future provider behavior. Cost distributions are available only with
complete persisted cost coverage.
Comparing otherwise compatible runs with different samples per task is allowed with an explicit
warning that their uncertainty estimates are not directly equivalent.

## Browse and compare persisted runs

These commands require the application `DATABASE_URL`, but no provider credentials, Redis, or
network access. They are read-only with respect to benchmark persistence.

```bash
uv run codejudge-benchmark list --limit 20
uv run codejudge-benchmark list --dataset codejudge-core@2
uv run codejudge-benchmark show <run-id>
uv run codejudge-benchmark compare <run-a> <run-b>
uv run codejudge-benchmark compare <run-a> <run-b> \
  --output benchmark-results/comparisons/<run-a>_vs_<run-b>/comparison.json
uv run codejudge-benchmark compare <run-a> <run-b> \
  --output benchmark-results/comparisons/<run-a>_vs_<run-b>/comparison.md
```

Comparison requires matching dataset/task/test fingerprints, benchmark and coding-prompt policy,
and scoring/evaluator semantics. A changed model configuration is allowed and reported as a warning.
Models match by stable `provider_id` plus `model`, never display name alone. Rates use explicit
percentage-point deltas. Missing evaluations and provider/evaluation failures remain missing or
named failures in per-task rows; they are not silently rewritten as score zero.

## Create and verify an archive

```bash
uv run codejudge-benchmark archive <run-id>
uv run codejudge-benchmark verify-archive benchmark-results/runs/<run-id>
```

`archive` refuses a nonempty destination and writes `results.json`, `report.md`, every referenced
candidate, and `manifest.json`. The archive-schema-v1 manifest records its creation timestamp,
CodeJudge version, run ID, dataset and benchmark-run fingerprints, expected files, and SHA-256 for
the results, report, and each candidate. Output ordering and hashes are independent of filesystem
enumeration order, locale, temporary paths, and terminal width; the creation timestamp is isolated
in the manifest.

`verify-archive` is offline: it does not load database or provider configuration. It rejects an
unsupported or malformed manifest, unsafe paths, symlinks, missing or unexpected files, any hash
mismatch, a run/fingerprint mismatch, and candidate references whose source hash differs from the
schema-v2 results. A mismatch returns nonzero.

Never place credentials, provider endpoints, authorization headers, database/Redis URLs, or raw
logs here. Export uses allowlisted persisted fields and scans for known configured secrets, but a
human must still review measured results, failures, limitations, cost/coverage, and provenance
before deliberate publication. Keep every archive intact; do not rewrite provenance to improve a
result. Historical schema-v1 exports remain historical records rather than being edited in place.

The benchmark rows lost before v0.7.4 are intentionally not reconstructed. Preserved static
schema-v2 files may be kept and verified as files, but archive verification never rehydrates them
into PostgreSQL.
