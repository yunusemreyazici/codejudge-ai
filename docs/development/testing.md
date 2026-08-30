# Testing

[← Project README](../../README.md) · [Documentation index](../README.md)

CodeJudge separates lightweight tests from PostgreSQL, Redis, Docker, and end-to-end suites. Tests
and CI use fakes for all AI and coding providers; no real provider call is required.

## Quality checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```

The lightweight suite requires no services:

```bash
uv run pytest -v -m "not sandbox and not database and not queue"
```

## PostgreSQL safety boundary

Destructive database and migration tests use only `CODEJUDGE_TEST_DATABASE_URL`. The name must end
in `_test`, and the destructive opt-in is mandatory. They never fall back to `DATABASE_URL`.

```bash
docker compose exec postgres createdb -U codejudge codejudge_test
export CODEJUDGE_TEST_DATABASE_URL=postgresql+asyncpg://codejudge:codejudge@127.0.0.1:5432/codejudge_test
export CODEJUDGE_ALLOW_DESTRUCTIVE_DATABASE_TESTS=1
export CODEJUDGE_REQUIRE_DATABASE=1
DATABASE_URL="$CODEJUDGE_TEST_DATABASE_URL" uv run alembic upgrade head
uv run pytest -v -m database tests/database
```

`CODEJUDGE_REQUIRE_DATABASE=1` turns missing infrastructure into a clear failure. Migration
subprocesses receive the validated dedicated URL explicitly. Never use `/codejudge` or any
development/production target here.

## Redis tests

Use a dedicated nonzero Redis database:

```bash
export CODEJUDGE_TEST_REDIS_URL=redis://127.0.0.1:6379/15
uv run pytest -v -m "queue and not worker_e2e" tests/queue
```

## Docker sandbox tests

```bash
docker build -t codejudge-python-sandbox:phase2 sandbox/
CODEJUDGE_REQUIRE_DOCKER=1 uv run pytest -v -m sandbox tests/sandbox
```

The required flag makes missing Docker capability fail rather than skip. The suite verifies normal
and failing code, syntax errors, timeout/OOM distinction, ambiguous SIGKILL handling, exact-event
filtering, memory/swap configuration, cleanup ordering, network denial, read-only filesystems,
non-root identity, Docker socket absence, secret isolation, PID limits, bounded output, and hidden-
test privacy.

## AI and worker end-to-end tests

All provider behavior is fake and local:

```bash
uv run pytest -v -m ai tests/ai
CODEJUDGE_REQUIRE_DOCKER=1 uv run pytest -v tests/queue/test_ai_worker_e2e.py
CODEJUDGE_REQUIRE_DOCKER=1 uv run pytest -v tests/queue/test_worker_e2e.py
CODEJUDGE_REQUIRE_DOCKER=1 uv run pytest -v tests/queue/test_benchmark_worker_e2e.py
```

## Authoritative guarded full suite

With PostgreSQL, Redis, and Docker already available, use this exact command:

```bash
CODEJUDGE_TEST_DATABASE_URL=postgresql+asyncpg://codejudge:codejudge@127.0.0.1:5432/codejudge_test \
CODEJUDGE_ALLOW_DESTRUCTIVE_DATABASE_TESTS=1 \
CODEJUDGE_REQUIRE_DATABASE=1 \
CODEJUDGE_TEST_REDIS_URL=redis://127.0.0.1:6379/15 \
CODEJUDGE_REQUIRE_DOCKER=1 \
uv run pytest -v
```

`DATABASE_URL` is intentionally unnecessary: destructive tests resolve only the dedicated test
setting. Before and after release validation, record the development database's Alembic revision,
schema fingerprint, and material row counts. They must not change.

## GitHub Actions mapping

The repository's `CI` workflow contains five jobs:

| Job | Coverage |
| --- | --- |
| `quality` | Ruff, format, mypy, migrated PostgreSQL/Redis, non-sandbox pytest |
| `sandbox` | Real Docker sandbox, privacy, task portfolio, cleanup |
| `ai-worker-e2e` | Fake-LLM, real-Docker, PostgreSQL/Redis worker flow |
| `benchmark-worker-e2e` | Fake coding models, real Docker, benchmark worker flow |
| `worker-e2e` | Ordinary PostgreSQL/Redis/Docker worker flow |

CI uses a dedicated `codejudge_test` database, Redis database 15, and explicit required-
infrastructure flags.
