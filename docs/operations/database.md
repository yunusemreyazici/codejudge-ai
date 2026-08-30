# Database operations and safety

[← Project README](../../README.md) · [Documentation index](../README.md)

PostgreSQL is CodeJudge's lifecycle and evidence authority. Redis notifications can be replayed;
persisted jobs, run plans, generation artifacts, and immutable evaluation snapshots define truth.

## Application database

Enable persistence and apply migrations explicitly:

```bash
export PERSISTENCE_ENABLED=true
export DATABASE_URL=postgresql+asyncpg://codejudge:codejudge@127.0.0.1:5432/codejudge
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
```

CodeJudge uses Alembic migrations and does not call `create_all()` at startup. Back up persistent
deployments before migration and verify the expected head. Application startup must not silently
rewrite historical benchmark or evaluation rows.

Evaluation snapshots are append-only reproducibility records. They store exact source and source
hashes, task/test identity, runtime and analyzer identity, official-test evidence, scores, findings,
sandbox identity, and optional AI provenance. Historical reads and aggregates do not rerun code or
contact providers.

The reproducibility fingerprint is a canonical SHA-256 identity over the source hash, task and test
fingerprints, analyzer-version map, scoring-policy version, execution backend, sandbox image tag
and local image ID when available, and CodeJudge version. A match records matching allowlisted
inputs and environment metadata; it does not prove identical CPU scheduling, host-kernel behavior,
or every source of runtime nondeterminism.

Public APIs expose no snapshot update or delete operation, and PostgreSQL triggers reject snapshot
`UPDATE` and `DELETE` as defense in depth. Identical submissions still receive distinct evaluation
IDs unless async request idempotency deliberately resolves a replay to an existing job.

## Hard boundary for destructive tests

Migration and destructive database tests resolve only:

```text
CODEJUDGE_TEST_DATABASE_URL
```

They never fall back to ambient `DATABASE_URL`. The target must use PostgreSQL, have a database
name ending in `_test`, and pass the explicit destructive-test opt-in:

```text
CODEJUDGE_ALLOW_DESTRUCTIVE_DATABASE_TESTS=1
```

The normal accepted local target is:

```text
postgresql+asyncpg://codejudge:codejudge@127.0.0.1:5432/codejudge_test
```

The normal development target ending in `/codejudge` is rejected. A missing dedicated URL fails
when `CODEJUDGE_REQUIRE_DATABASE=1` and otherwise skips database-dependent tests with a safe
diagnostic. If the dedicated target resolves to the same non-test database as `DATABASE_URL`, it is
rejected rather than trusted through variable precedence.

Migration tests pass the explicitly resolved safe URL into Alembic subprocesses. Subprocess
inheritance cannot select another database.

## Prepare the test database

Create it once if needed:

```bash
docker compose exec postgres createdb -U codejudge codejudge_test
```

Then use the exact guarded environment:

```bash
export CODEJUDGE_TEST_DATABASE_URL=postgresql+asyncpg://codejudge:codejudge@127.0.0.1:5432/codejudge_test
export CODEJUDGE_ALLOW_DESTRUCTIVE_DATABASE_TESTS=1
export CODEJUDGE_REQUIRE_DATABASE=1
DATABASE_URL="$CODEJUDGE_TEST_DATABASE_URL" uv run alembic upgrade head
```

Do not point these variables at development or production. Destructive migration tests may upgrade
and downgrade through the full migration history.

## Operational checks

- Treat schema revision, schema fingerprint, and material row counts as before/after safety evidence
  around test or release work.
- Keep test credentials and databases distinct even on a developer workstation.
- Never reconstruct lost historical benchmark rows from reports; preserve surviving snapshots.
- Never edit benchmark artifacts or evaluation snapshots to match current runtime semantics.
- Use read-only `list`, `show`, and `compare` commands when auditing benchmark history.

The authoritative full-suite command is in [Testing](../development/testing.md).
