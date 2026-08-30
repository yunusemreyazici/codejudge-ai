# Workers, delivery, and leases

[← Project README](../../README.md) · [Documentation index](../README.md)

CodeJudge has separate commands for ordinary evaluation work and benchmark generation work:

```bash
uv run codejudge-worker
uv run codejudge-benchmark-worker
```

Both rely on PostgreSQL as lifecycle authority and Redis Streams as recoverable delivery.

## Transactional outbox

The API commits a queued record and corresponding outbox event in one PostgreSQL transaction. An
outbox publisher performs `XADD` and records publication only after Redis accepts the message. A
Redis outage therefore leaves durable work queued for later publication rather than losing it.

Consumer groups retain unacknowledged messages. Stale pending delivery can be claimed by another
worker. Delivery is at least once; correctness comes from idempotent durable transitions, not an
exactly-once transport claim.

## Claims and leases

Workers atomically claim queued work and persist an owner plus expiry. Active work renews its lease
on an interval bounded relative to the lease duration. Safety invariants include:

- renewal cannot succeed at or after persisted expiry;
- ownership loss cancels active work;
- an unconfirmed renewal before expiry fails closed;
- stale owners cannot finalize;
- dead-worker work can be reclaimed;
- attempts remain bounded by policy;
- successful completion and evaluation persistence cannot be duplicated.

The default worker lease is 60 seconds and maximum infrastructure attempts are three. Successive
retry delays follow the current deterministic 5, 15, and 45 second schedule; with three total
attempts, only the first two failures schedule another attempt.

## Candidate outcomes versus infrastructure failure

Candidate syntax errors, failed tests, explicit timeout, authoritative OOM, and ordinary findings
produce terminal evaluation snapshots and are not worker retries. Infrastructure failures may
retry within policy. Integrity failures—such as persisted CodeJudge version or evaluator identity
not matching the runtime—are terminal and never bypassed.

Before running source, the worker validates exact source hash, task/test fingerprints, analyzer
versions, scoring policy, CodeJudge version, and sandbox image identity. A queued job must represent
the runtime that created it.

## Benchmark worker

The benchmark worker resolves the configured provider capabilities, claims benchmark samples,
generates source, persists a nonblank artifact, and calls the ordinary evaluator. A provider-level
semaphore is shared by all models using that provider inside the process. Generation failure
categories and allowlisted details are normalized and bounded before persistence.

The benchmark worker does not create reports or alter scoring semantics. Productization commands
aggregate stored artifacts and evaluations separately.

## Health and recovery

`GET /health/queue` reports Redis capability and a TTL-backed active-worker count. Treat the count
as liveness information, not durable ownership truth. PostgreSQL lease and lifecycle state remain
authoritative.

Operators should alert on sustained unpublished outbox events, repeated lease expiry, exhausted
attempts, unavailable sandbox capability, and labeled container leakage. Restarting Redis or a
worker must not require editing database rows manually.
