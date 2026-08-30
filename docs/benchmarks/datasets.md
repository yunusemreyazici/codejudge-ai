# Benchmark datasets

[← Project README](../../README.md) · [Documentation index](../README.md)

Datasets are repository-versioned evaluation contracts, not mutable task lists. Their identity and
fingerprint bind ordered tasks, task versions, public prompts, official tests, references, weights,
and benchmark policy material.

## Dataset versions

`codejudge-core@1` is the immutable original single-task LRU-cache dataset. It remains available so
historical Phase 7 runs retain their meaning.

`codejudge-core@2` is the current seven-task engineering portfolio. Its preserved historical
fingerprint is:

```text
ee0f631d6810c039e84d90d9f2b77f20dcabbe27bef0af600695ab9cb1111988
```

The v2 tasks have equal weights:

| Task | Engineering focus |
| --- | --- |
| LRU Cache | Data structures and recency state |
| TTL Cache | Expiration, state, and eviction |
| Sliding-Window Rate Limiter | Time-window algorithms and per-key state |
| Retry Backoff | Deterministic reliability arithmetic |
| Dependency Resolver | Graphs, cycles, and stable ordering |
| Async Batch Processor | asyncio, bounded concurrency, and cleanup |
| Circuit Breaker | Explicit reliability state machines |

The portfolio intentionally spans stateful data structures, time, graphs, async coordination, and
reliability state machines. A model cannot dominate through one narrow implementation pattern.

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
[Benchmark Design](../BENCHMARK_DESIGN.md).
