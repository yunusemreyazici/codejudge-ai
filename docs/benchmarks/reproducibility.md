# Benchmark reproducibility

[← Project README](../../README.md) · [Documentation index](../README.md)

CodeJudge captures enough immutable identity to explain and compare a run without pretending that
an externally hosted model is perfectly reproducible.

## Captured identity

Run and sample provenance includes:

- dataset ID, version, fingerprint, ordered logical task IDs, immutable task revisions, weights,
  and task/test fingerprints;
- public coding-prompt version and hash;
- benchmark-policy version;
- provider and model identities, parameters, output mode, timeout, and concurrency;
- pricing version, tokens, currencies, and calculated costs;
- generation attempt count, latency, normalized failure, source bytes, and source hash;
- execution-time CodeJudge version, scoring-policy version, analyzer versions, and sandbox identity;
- deterministic results and optional AI identities kept as separate evidence.

Provider seeds and response hashes improve provenance but cannot freeze a hosted provider backend.

Released dataset entries that predate explicit task revisions canonically resolve to revision 1.
New manifests include `task_revision`. The dataset registry verifies the exact revision's public
task version and task/test fingerprints before planning. The benchmark worker resolves that same
entry from the persisted run's immutable dataset identity before generation and evaluation; it
does not fall back to the registry's current revision. A run therefore remains tied to the same
evaluator material even after another revision of its logical task is added.

## Historical inspection

These commands read persisted runs and immutable evaluation snapshots. They do not construct a
provider or modify benchmark state:

```bash
uv run codejudge-benchmark list --limit 20
uv run codejudge-benchmark list --dataset codejudge-core@2
uv run codejudge-benchmark show <RUN_ID>
uv run codejudge-benchmark compare <RUN_A> <RUN_B>
```

Model matching uses `provider_id` plus `model`. Changed model-configuration fingerprints are
reported. Incompatible dataset, task/test, prompt, benchmark-policy, scoring, or evaluator semantics
produce no metric deltas and a nonzero exit. A samples-per-task change is disclosed as a warning.

## Export and report

```bash
uv run codejudge-benchmark export <RUN_ID>
uv run codejudge-benchmark report <RUN_ID>
```

Exports contain canonical `results.json` and UUID-named candidate files. Reports include a
Markdown rendering and the exact results JSON SHA-256. Candidate source hashes are recomputed
byte-for-byte before export. Nonterminal runs require explicit `--allow-incomplete`; failed runs do
not get a misleading leaderboard.

Normal generated output is Git-ignored. Review it before intentionally publishing any artifact.
See the [benchmark artifact policy](../../benchmark-results/README.md).

## Immutable local archives

```bash
uv run codejudge-benchmark archive <RUN_ID>
uv run codejudge-benchmark verify-archive benchmark-results/runs/<RUN_ID>
```

Archive creation writes only a new local directory, refuses to overwrite a nonempty target, and
does not register or publish anything in PostgreSQL. The manifest binds results, report, and every
candidate file by SHA-256. `verify-archive` is offline and does not load database settings.

Archive-creation `codejudge_version` is not automatically the execution-time version. Historical
claims must use the version stored on each evaluation snapshot; an archive may be created later by
a newer runtime.

An archive is an immutable historical artifact. A newer CodeJudge version may derive stricter
database-backed correctness, eligibility, or presentation from preserved rows while the archived
JSON and Markdown remain unchanged. Current documentation should label the newer derived view and
must not rewrite the archive to erase the historical reporting context.

## Preserving history

- Never edit persisted benchmark rows to restate old metrics.
- Never normalize historical candidate source or provider failures after the fact.
- Never regenerate a report from provider calls when stored evidence is sufficient.
- Record semantic incompatibility instead of forcing cross-run deltas.
- Keep benchmark configs and archives unchanged during documentation, release, and test work unless
  their mutation is the explicit task.
- Never repoint a released dataset entry at a newer task revision. Publish a new dataset identity
  and retain every revision needed to replay history.
