# Benchmark datasets

[← Project README](../../README.md) · [Documentation index](../README.md)

Datasets are repository-versioned evaluation contracts, not mutable task lists. Their identity and
fingerprint bind ordered tasks, task versions, public prompts, official tests, references, weights,
and benchmark policy material.

## Dataset versions

`codejudge-core@1` is the immutable original single-task LRU-cache dataset. It remains available so
historical Phase 7 runs retain their meaning.

`codejudge-core@2` is the immutable seven-task engineering portfolio used by all currently
published benchmark results. Its preserved historical fingerprint is:

```text
ee0f631d6810c039e84d90d9f2b77f20dcabbe27bef0af600695ab9cb1111988
```

`codejudge-core@3` is the current twelve-task portfolio. It preserves all seven v2 task identities
and adds five tasks for capabilities that v2 did not cover. Its fingerprint is:

```text
1191d27db4643e9c18a0063ea9da1d2fb56fc363f0d2146740b53eee05e94522
```

All v2 and v3 tasks have equal weight `1.0`. Published v2 results are not v3 results and must not be
presented as though they covered the expanded task set. No model leaderboard data was generated
for v3 as part of the dataset implementation.

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

The original selection rationale and common-bug discussion remain in
[Benchmark Design](../BENCHMARK_DESIGN.md). The current mutation-based discrimination evidence and
known released-suite gaps are recorded in [Task quality](task-quality.md).
