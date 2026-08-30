# Benchmark task quality

[← Project README](../../README.md) · [Documentation index](../README.md) ·
[Datasets](datasets.md)

This page records a structural discrimination audit of the twelve tasks in
`codejudge-core@3`. It does not report model accuracy. No provider was invoked, and no historical
dataset, benchmark run, or archive was changed.

## Method

The audit materializes named, reviewable source mutations from each trusted reference. Mutants
represent plausible implementation mistakes rather than syntax or import failures. The canonical
identity-bound evaluator runs every mutant; one representative killed mutant per task and every
initial survivor are also checked through the private-safe Docker official harness.

The mutation score is `killed / (killed + survived)`. Behaviorally equivalent mutants and
infrastructure-invalid executions are reported separately and excluded from the denominator. A
high score shows discrimination against this mutation sample, not complete coverage or inherent
task difficulty.

## Task-quality audit

The Docker times below are single local observations and include container/control overhead. They
are approximate, not performance budgets.

| Task ID | Primary capability | Official cases | Edge density | Statefulness | Algorithmic complexity | Error density | Pre-audit incorrect fixture | Reference Docker time | Structural difficulty |
| --- | --- | ---: | --- | --- | --- | --- | --- | ---: | --- |
| `async-batch-processor` | Bounded async coordination | 6 | High | Concurrent batch | Medium | High | Yes | 1.65s | High |
| `circuit-breaker` | Time-driven state machine | 7 | High | Strong | Low–medium | High | Yes | 1.85s | High |
| `config-layer-merge` | Recursive transformation | 12 | High | Stateless | Medium | High | Yes | 2.99s | Medium–high |
| `dependency-resolver` | Stable topological sort | 12 | High | Stateless | High | High | Yes | 2.94s | High |
| `frame-decoder` | Incremental streaming parser | 17 | Very high | Strong | Medium | Very high | Yes | 4.08s | High |
| `interval-reservation` | Half-open interval state | 13 | High | Strong | Medium | High | Yes | 3.18s | Medium–high |
| `logical-path` | Lexical normalization | 10 | High | Stateless | Low–medium | High | Yes | 2.45s | Medium |
| `lru-cache` | Recency-aware data structure | 8 | Medium | Strong | Medium | Low | No | 2.07s | Low–medium |
| `rate-limiter` | Sliding time window | 7 | High | Strong | Medium | High | Yes | 1.82s | Medium–high |
| `retry-backoff` | Capped arithmetic | 14 | High | Stateless | Medium | High | Yes | 3.30s | Medium |
| `structured-event-parser` | Validation and normalization | 17 | Very high | Within-call sequence | Medium | Very high | Yes | 3.89s | Medium–high |
| `ttl-cache` | Expiry plus LRU state | 7 | High | Strong | Medium | High | Yes | 1.78s | High |

The missing pre-audit LRU fixture was portfolio-test drift, not an official-task defect. The audit
adds an LRU incorrect candidate so the ordinary portfolio rejection checks now cover all twelve
tasks.

## Mutation results

| Task ID | Generated | Valid | Killed | Survived | Equivalent | Invalid | Score |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `async-batch-processor` | 6 | 5 | 5 | 0 | 1 | 0 | 100% |
| `circuit-breaker` | 6 | 6 | 6 | 0 | 0 | 0 | 100% |
| `config-layer-merge` | 6 | 6 | 6 | 0 | 0 | 0 | 100% |
| `dependency-resolver` | 6 | 6 | 6 | 0 | 0 | 0 | 100% |
| `frame-decoder` | 6 | 6 | 5 | 1 | 0 | 0 | 83.3% |
| `interval-reservation` | 6 | 6 | 6 | 0 | 0 | 0 | 100% |
| `logical-path` | 6 | 6 | 6 | 0 | 0 | 0 | 100% |
| `lru-cache` | 6 | 6 | 6 | 0 | 0 | 0 | 100% |
| `rate-limiter` | 6 | 5 | 5 | 0 | 1 | 0 | 100% |
| `retry-backoff` | 6 | 6 | 5 | 1 | 0 | 0 | 83.3% |
| `structured-event-parser` | 6 | 5 | 5 | 0 | 1 | 0 | 100% |
| `ttl-cache` | 6 | 6 | 4 | 2 | 0 | 0 | 66.7% |
| **Portfolio** | **72** | **69** | **65** | **4** | **3** | **0** | **94.2%** |

The three excluded equivalents are deliberate and documented in the catalog:

- cancellation propagation supplied by the awaited `asyncio.gather` preserves the observable
  batch-cleanup contract without the broader catch;
- pruning one expired rate-limit event per call is sufficient for every observable allow decision
  because the queue never exceeds the limit;
- a stable timestamp sort cannot reorder valid events whose timestamps are already required to be
  nondecreasing.

## Surviving mutants and immutable-dataset decision

Four plausible mutants expose contract-covered gaps in the released cases:

- `frame-decoder` does not distinguish Python character length from UTF-8 byte length when a
  Unicode payload is split before all declared characters arrive;
- `retry-backoff` does not exercise the allowed boundary `base_delay == max_delay`;
- `ttl-cache` can miss a pre-put purge when an expired most-recent entry masks eviction of a live
  least-recent entry;
- `ttl-cache` does not directly delete an already-expired entry before another operation purges it.

These cases are inferable from the public contracts, but `codejudge-core@3` is released and remains
immutable. Its entries canonically select revision 1, whose specifications, references, and
official cases are not patched in place. The version-aware registry can now retain revision 1 while
a future dataset selects revision 2 for corrected tasks. No core@4 or revision-2 correction was
created by this architectural change; the four findings above remain the released core@3 result.

When those gaps are addressed, a future core@4 can reference revision 2 for `frame-decoder`,
`retry-backoff`, and `ttl-cache` while leaving unaffected tasks on revision 1. The mutation audit
resolves tasks through the selected dataset, so audits of core@3 continue exercising the exact
revision-1 material rather than whichever revision is configured as current.

## Portfolio balance and case density

The set balances six strongly stateful tasks with stateless parsing, graph, path, merge, and
arithmetic work. It includes one asynchronous task, two parsing/streaming tasks, one graph task,
four tasks with explicit time semantics, recursive transformation, data structures, boundary-heavy
algorithms, and substantial validation coverage. The main concentration is deterministic Python
service logic; async breadth and graph breadth each rely on one task.

Official case counts range from 6 to 17, but task weight remains one per task. Higher counts mostly
come from separating invalid input types and boundary variants so the trusted host protocol can
name failures precisely. No case is an obvious redundant removal candidate: similar validation
cases exercise distinct accepted/rejected types, and removing them would reduce discrimination
without changing benchmark weight. Frame and structured-event cases are the most runtime-dense;
their roughly four-second local Docker observations remain below the eight-second task ceiling.

| Task ID | Cases | Distinct semantic categories | Removal candidate |
| --- | ---: | --- | --- |
| `async-batch-processor` | 6 | validation, order, concurrency, failure cleanup, cancellation, oversized concurrency | None |
| `circuit-breaker` | 7 | reset-on-success, threshold, validation, half-open success/failure, time order, reset | None |
| `config-layer-merge` | 12 | recursive merge, deletion, replacement, isolation, outer shape, key validation, nested retention | None |
| `dependency-resolver` | 12 | base graphs, unknown leaves, tie-breaks, cycles, diamond, duplicates, components, immutability, shape | None |
| `frame-decoder` | 17 | multi-frame, chunking, buffering, limits, prefix validation, reset, finish, Unicode payload | None |
| `interval-reservation` | 13 | overlap/adjacency, resources, cancellation, ID scope, validation, containment, copy isolation | None |
| `logical-path` | 10 | absolute/relative, cwd normalization, validation, root boundary, traversal, backslash, replacement | None |
| `lru-cache` | 8 | insertion, missing, update, eviction, access recency, capacity one, repeated eviction | None |
| `rate-limiter` | 7 | limit/rejection, exact boundary, validation, keys, pruning, time order, instances | None |
| `retry-backoff` | 14 | growth, cap, custom factor, attempt validation, delay validation, purity | None |
| `structured-event-parser` | 17 | normalization, blanks, container/item types, JSON/schema validation, uniqueness/order, copy isolation | None |
| `ttl-cache` | 7 | overwrite/missing, expiry, validation, capacity/LRU, stale capacity, overwrite recency, deletion/instances | None |

Representative killed-mutant Docker times tracked their references closely (approximately
1.65–4.15 seconds), so mutation execution did not reveal a disproportionate algorithmic runtime or
justify changing resource limits or timeouts.
