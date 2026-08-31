# Deployment

[← Project README](../../README.md) · [Documentation index](../README.md)

CodeJudge is a Python 3.13 FastAPI service with PostgreSQL, Redis, evaluator workers, benchmark
workers, and a prebuilt Docker sandbox image. This page describes the repository's implemented
deployment contract, not a claim of turnkey hostile multi-tenant hardening.

## Startup order

1. Start PostgreSQL 17 and Redis 7.
2. Set application environment and secrets through the deployment secret store.
3. Apply Alembic migrations once.
4. Build or pull the reviewed sandbox image.
5. Verify Docker capability and image identity.
6. Start evaluation workers.
7. Start a benchmark worker only when benchmark execution is intentionally enabled.
8. Start the FastAPI process and check all capability endpoints.

```bash
docker compose up -d postgres redis
export PERSISTENCE_ENABLED=true
export EVALUATION_MODE=async
export DATABASE_URL=postgresql+asyncpg://codejudge:codejudge@127.0.0.1:5432/codejudge
export REDIS_URL=redis://127.0.0.1:6379/0
uv run alembic upgrade head
docker build -t codejudge-python-sandbox:phase2 sandbox/
uv run codejudge-worker
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Important settings

| Variable | Default | Purpose |
| --- | ---: | --- |
| `APP_ENV` | `development` | Deployment environment label |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `DEFAULT_EXECUTION_TIMEOUT` | `5.0` | Fallback task timeout |
| `MAX_CODE_SIZE` | `102400` | Maximum UTF-8 submission bytes |
| `EXECUTION_BACKEND` | `docker` | `docker` or explicit development-only `local` |
| `PERSISTENCE_ENABLED` | `false` | Require PostgreSQL snapshots |
| `DATABASE_URL` | unset | Required `postgresql+asyncpg://` URL when persistence is enabled |
| `EVALUATION_MODE` | `sync` | Synchronous compatibility or durable `async` mode |
| `REDIS_URL` | unset | Required `redis://` or `rediss://` URL for async mode |
| `WORKER_CONCURRENCY` | `1` | Consumer slots per worker process |
| `WORKER_LEASE_SECONDS` | `60` | Renewable PostgreSQL dead-worker detection window; not a sample runtime limit |
| `WORKER_MAX_ATTEMPTS` | `3` | Maximum infrastructure attempts |
| `OUTBOX_POLL_INTERVAL_SECONDS` | `1` | Publisher and maintenance interval |
| `RETRY_BASE_DELAY_SECONDS` | `5` | Retry backoff base |

Sandbox-specific defaults are listed in [Resource limits](../sandbox/resource-limits.md).

Optional AI assessment requires persistence plus `LLM_BASE_URL`, `LLM_API_KEY`, one or more
`LLM_JUDGE_MODEL`/`LLM_JUDGE_MODELS` values, and `LLM_ADVERSARIAL_MODEL`. Default safeguards are a
30-second request timeout, two provider attempts, 2,000 output tokens, 100,000 input bytes, 262,144
response bytes, and five generated adversarial tests.

Benchmark execution requires persistence, Redis, `BENCHMARK_ENABLED=true`, and either
`BENCHMARK_CONFIG` or the legacy direct benchmark-provider settings. The default generation worker
concurrency is two; provider-specific semaphores may reduce it. See
[Providers and budgets](../benchmarks/providers.md) for run limits and commit-safe configuration.

Defaults are conservative for local use, but capacity and threat assumptions are deployment
specific. Do not weaken read-only filesystems, network denial, non-root execution, swap policy,
capability drops, or cleanup to improve throughput.

## Health endpoints

- `GET /health` — API process liveness.
- `GET /health/sandbox` — configured runner capability.
- `GET /health/database` — PostgreSQL capability without changing basic liveness semantics.
- `GET /health/queue` — Redis capability and active worker heartbeat count.

Use capability checks for readiness according to the enabled deployment mode. A Docker backend
failure returns a sanitized `503`; it never falls back to local execution.

## Secrets and network placement

Provider endpoints, provider credentials, database URLs, and Redis URLs belong in environment
variables supplied by a secret manager. Benchmark YAML may contain only the names of provider
environment variables. Do not log environment dumps or provider response bodies.

Candidate containers use `network=none`, receive a minimal environment, and must not receive the
Docker socket. Restrict daemon access on the host. Protect the API, PostgreSQL, Redis, generated
reports, and candidate archives with appropriate authentication and network controls; application
authentication is not provided by this repository's current phase.

## Production cautions

- Docker shares the host kernel; consider stronger isolation for highly hostile multi-tenancy.
- Scale workers only with tested database and Redis capacity, and keep provider semaphores intact.
- Back up PostgreSQL before migration and verify the expected Alembic revision.
- Monitor outboxes, leases, retries, sandbox cleanup, disk use, and provider failure rates.
- Keep ordinary development databases completely separate from destructive test databases.

Read [Security model](../sandbox/security-model.md), [Workers](workers.md), and
[Database operations](database.md) before deployment.
