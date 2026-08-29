# Benchmark result artifacts

`generated/<run-id>/` is the default output for `codejudge-benchmark export` and
`codejudge-benchmark report`. It is ignored by Git because generated output is not evidence that
a real benchmark was reviewed or approved for publication.

After a human verifies measured results, source hashes, cost and coverage metadata, failures,
limitations, and the provenance appendix, selected artifacts may be copied deliberately into a
named directory under `published/`. CodeJudge never commits, pushes, publishes, or edits the main
README automatically.

Keep `results.json`, `report.md`, and the referenced `candidates/` files together. Do not remove or
rewrite provenance to make a result look better. Never place credentials, provider endpoints,
authorization headers, database URLs, Redis URLs, or raw logs here. The exporter builds artifacts
from allowlisted persisted fields and performs a best-effort secret scan, but human review remains
required.

Current exports use results schema version `2`. The primary weighted deterministic mean measures
quality over completed evaluations only and must be read with coverage. Correctness pass uses
completed evaluations with zero failed official tests; end-to-end success uses all planned samples;
perfect deterministic score means exactly 100 and is not a synonym for correctness. The
coverage-adjusted deterministic score assigns zero to missing planned evaluations while preserving
task weights.

> Coverage-adjusted score is supplemental and intentionally penalizes missing planned evaluations.
> It must not be confused with the primary successful-evaluation quality score.

Reports keep provider generation latency, sandbox test execution duration, and the longer
sample-to-snapshot evaluation lifecycle duration separate. Cost-per-success fields are unknown or
not applicable when their denominator is zero or generation-cost coverage is incomplete—never
zero by assumption. Historical version-1 artifacts remain historical records; regenerate from the
same immutable database snapshots to obtain version-2 names and metrics rather than editing an
artifact in place.
