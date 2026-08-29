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
