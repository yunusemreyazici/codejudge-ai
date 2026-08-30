# Benchmark datasets

[← Project README](../../README.md) · [Documentation index](../README.md)

Datasets are repository-versioned evaluation contracts, not mutable task lists. Their identity and
fingerprint bind ordered tasks, task versions, public prompts, official tests, references, weights,
and benchmark policy material.

## Logical tasks and immutable revisions

A task has a stable logical ID, such as `ttl-cache`, and an immutable evaluator revision, such as
`ttl-cache@1`. The public task-specification version remains separate: it describes the public
contract, while the integer revision selects the exact repository-backed specification, reference,
and official-test material used to evaluate it.

Dataset entries resolve by logical ID **and exact revision**. Benchmark planning, generation,
evaluation, evaluator identity, and mutation auditing all use that dataset-selected revision; none
of those historical paths consult the current/default revision. Normal task listing still shows
one deliberately configured current revision per logical task so the API remains concise.

The existing definition directory for each task is explicitly revision 1. Future revisions live
beside it under a numbered `revisions/` directory. There is no authoritative `latest` filesystem
alias. Released manifests omit the revision field for backward compatibility, and omission has the
single canonical meaning revision 1. New dataset manifests must record `task_revision` explicitly.
This preserves the released JSON and hashes while allowing multiple revisions to coexist.

## Dataset versions

`codejudge-core@1` is the immutable original single-task LRU-cache dataset. It remains available so
historical Phase 7 runs retain their meaning.

`codejudge-core@2` is the immutable seven-task engineering portfolio used by all currently
published benchmark results. Its preserved historical fingerprint is:

```text
ee0f631d6810c039e84d90d9f2b77f20dcabbe27bef0af600695ab9cb1111988
```

`codejudge-core@3` is the immutable original twelve-task portfolio. It preserves all seven v2 task identities
and adds five tasks for capabilities that v2 did not cover. Its fingerprint is:

```text
1191d27db4643e9c18a0063ea9da1d2fb56fc363f0d2146740b53eee05e94522
```

`codejudge-core@4` retains the same twelve logical tasks and equal weights while selecting revision
2 for `frame-decoder`, `retry-backoff`, and `ttl-cache`. The other nine tasks remain revision 1.
Its fingerprint is:

```text
ed5b1a5c0263ca6d172c31c15de910795815247f238cfefc3975624ce4f296d0
```

The revised evidence distinguishes Python character counts across Unicode frame chunks, accepts
the documented equal retry base/cap boundary, purges expired TTL entries before capacity eviction,
and treats deletion of an expired entry as unsuccessful. Public contracts, task weights, logical
task IDs, timeouts, and trusted reference behavior are unchanged.

All v2, v3, and v4 tasks have equal weight `1.0`. Published v2 results are not v3 or v4 results and
must not be presented as though they covered either twelve-task dataset. No model leaderboard data
was generated for v3 or v4 as part of their dataset implementations.

All tasks referenced by core@1, core@2, and core@3 resolve to revision 1. Their files and official
behavior remain frozen. Core@4 records every revision explicitly and cannot redirect historical
resolution even though the three hardened revisions are now the normal current/default tasks.

## Capability portfolio

| Task | State | Async | Parsing | Graph | Time | Algorithms | Error/edge-case focus |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LRU Cache | Strong | — | — | — | — | Recency/eviction | Capacity and update ordering |
| TTL Cache | Strong | — | — | — | Strong | Recency/expiry | Exact expiry boundary |
| Sliding-Window Rate Limiter | Strong | — | — | — | Strong | Window pruning | Rejections and per-key order |
| Retry Backoff | — | — | — | — | Moderate | Bounded arithmetic | Types, caps, and overflow avoidance |
| Dependency Resolver | — | — | — | Strong | — | Stable topological sort | Cycles, duplicates, unknown leaves |
| Async Batch Processor | Moderate | Strong | — | — | — | Bounded coordination | Failure/cancellation cleanup |
| Circuit Breaker | Strong | — | — | — | Strong | State transitions | Probe boundaries and reset |
| Structured Event Parser | — | — | Strong | — | Moderate | Validation/normalization | Malformed records, duplicates, order |
| Interval Reservation | Strong | — | — | — | — | Half-open overlap | Adjacency, containment, cancellation |
| Configuration Layer Merge | Moderate | — | — | — | — | Recursive transformation | Deletion, replacement, aliasing |
| Logical Path | — | — | Moderate | — | — | Lexical normalization | Root traversal and separators |
| Frame Decoder | Strong | — | Strong | — | — | Incremental state machine | Chunking, truncation, bounded frames |

The v3 portfolio materially broadens v2's state, time, graph, async, and reliability coverage with
structured validation, interval boundaries, recursive transformations, lexical path rules, and
streaming parser state. A model cannot dominate through one narrow implementation pattern.

## Public prompt and private evidence

Coding providers receive only the public task specification and a versioned prompt asking for
source. They do not receive official hidden tests, reference implementations, expected outputs,
evaluator paths, credentials, or another model's candidate.

Candidate containers contain only `solution.py`. Official assertions are kept in the trusted
host-side harness and interact with candidate public APIs through a bounded protocol.

## Test and reference role

Official tests define correctness. Trusted references validate task behavior and any optional
AI-generated adversarial tests. Generated tests are supplemental; they do not become official
hidden tests and cannot change deterministic correctness.

Task and test fingerprints are persisted with each evaluation. Comparison is rejected when dataset,
task/test, prompt, benchmark-policy, scoring, or evaluator semantics are incompatible.

## Changing a dataset

Do not edit a published dataset identity in place. Material changes require a new dataset version
and fingerprint. Review additions for:

- deterministic behavior and bounded execution;
- stable public contracts and unambiguous language requirements;
- useful hidden cases without leaking assertions through prompts;
- trustworthy expected test counts;
- reference correctness and sandbox compatibility;
- balanced portfolio coverage and documented task weights.

Contract-covered corrections to an existing task require a new immutable task revision and a new
dataset version. Core@4 demonstrates this by selecting revision 2 only for three corrected tasks
and retaining revision 1 for unaffected tasks without patching core@3 in place.

The original selection rationale and common-bug discussion remain in
[Benchmark Design](../BENCHMARK_DESIGN.md). The current mutation-based discrimination evidence and
known released-suite gaps are recorded in [Task quality](task-quality.md).
