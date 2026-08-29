# Benchmark Design

CodeJudge Core is a small software-engineering benchmark, not a catalogue of isolated syntax
exercises. Each task has a public contract, versioned official tests, a private trusted reference,
and stable task/test fingerprints. Generated candidates always pass through the same Docker,
static-analysis, scoring, and snapshot pipeline used by ordinary CodeJudge evaluations.

Coding-generation transport is an explicit provider capability, never inferred from a provider
name. `structured_json` is the default and validates the schema before evaluation. `raw_source`
preserves complete non-empty assistant content exactly, even when it contains Markdown fences or
prose; the evaluator then determines whether those bytes form a valid solution. Output mode and
bounded request timeout and provider concurrency limit are persisted in model provenance and
fingerprints. Direct comparisons should use one shared mode; mixed-mode runs are explicitly
disclosed. One provider adapter object is shared across models in a worker process, so an explicit
provider semaphore prevents those models from bypassing the configured request limit.

## Dataset versions

`codejudge-core@1` remains the immutable single-task Phase 7 dataset. `codejudge-core@2` adds six
tasks while retaining the exact v1 LRU identity. Every v2 task has weight `1.0`; weights are not
tuned around any model or desired leaderboard result.

Changing a public contract or official test changes its fingerprint and requires a new task and
dataset version. Dataset files reference task identities but contain neither test source nor
reference implementations.

## Portfolio

| Task | Skill under test | Deterministic design |
| --- | --- | --- |
| LRU Cache | data structures and eviction state | operation sequence only |
| TTL Cache | expiration, state, and eviction | caller-supplied logical timestamps |
| Rate Limiter | sliding-window algorithms | caller-supplied per-key timestamps |
| Retry Backoff | reliability arithmetic | pure function; no sleeping or jitter |
| Dependency Resolver | graphs and cycle detection | lexical tie-breaking |
| Async Batch Processor | asyncio and bounded concurrency | events/barriers; no timing sleeps |
| Circuit Breaker | reliability state machines | caller-supplied logical timestamps |

The tasks are intentionally compact enough for repeated benchmark runs but cover materially
different failure modes. They avoid network, filesystem, randomness, external services, and real
wall-clock waits.

## Hidden-test philosophy and common bugs

Official tests target mistakes that plausible-looking implementations commonly make:

- **LRU Cache:** omit eviction, fail to refresh recency on reads, or grow when updating a key.
- **TTL Cache:** return expired values, use the wrong expiration boundary, or evict without first
  removing expired entries. Tests also distinguish overwrite and recency semantics.
- **Rate Limiter:** share one global counter across keys, retain events at the exact boundary, or
  record rejected attempts. Nonmonotonic time is rejected per key.
- **Retry Backoff:** use zero-based attempts, cap one attempt too early/late, or overflow for very
  large attempts. Invalid booleans and numeric ranges are covered.
- **Dependency Resolver:** return unstable DFS order, omit dependencies absent as keys, or miss
  cycles/self-cycles. Disconnected graphs, duplicate edges, and input mutation are checked.
- **Async Batch Processor:** exceed/ignore the limit, return completion order, or leak unfinished
  tasks after failure/cancellation. Event coordination proves parallelism without scheduler-time
  assumptions.
- **Circuit Breaker:** never block open calls, use the wrong recovery boundary, or mishandle a
  failed half-open probe. Success reset, timestamp validation, and explicit reset are covered.

Official assertions run in a trusted host harness. The candidate container mounts only
`solution.py`; a bounded JSON-lines protocol forwards one current public-API operation at a time to
a fresh candidate process for each case. Expected values, future operations, test source,
references, canaries, and evaluator paths remain outside the candidate interpreter and filesystem.
The canonical pytest files remain the versioned semantic source used for test fingerprints, and
the host plans preserve the same 61 case counts and behaviors. A repeated real-Docker malicious
filesystem probe guards this boundary. See the security model for protocol details and limitations.

## References and adversarial tests

Every task stores `reference/solution.py` beside its definition. References are packaged for
workers and used to reject invalid AI-generated adversarial tests. They are never returned by task
APIs, included in coding-model or judge payloads, written to Redis, or logged. The generic
reference-discovery and sandbox path is tested with both class-based and function-based tasks.

## Interpretation limits

Dataset results depend on task selection, public wording, official test quality, model parameters,
provider behavior, sampling, and evaluator identity. Seven compact Python tasks cannot represent
all software engineering, repository maintenance, framework knowledge, security engineering, or
long-horizon agent behavior. Leaderboards must therefore be read as controlled comparisons for
this exact dataset—not universal coding-intelligence rankings.
