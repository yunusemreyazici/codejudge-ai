# CodeJudge AI

> A production-oriented evaluation framework for testing and scoring AI-generated code.

CodeJudge AI is an open-source backend for reproducible code evaluation. Phase 5 adds durable
asynchronous jobs and distributed workers while retaining the restricted Docker execution path,
deterministic analysis, and immutable PostgreSQL snapshots.

> [!CAUTION]
> Docker materially strengthens isolation, but it is **not a perfect security boundary**. The
> Docker daemon, runtime, and host kernel remain trusted. Review the full
> [security model](docs/security.md) before handling hostile public submissions. The `local`
> backend is development-only and must never execute untrusted code.

## Overview

The service accepts a task ID, language, and candidate source code. The evaluation engine resolves
the task, dispatches it through the configured runner, and separately passes the exact immutable
source to a static-analysis engine. In the recommended Docker mode, execution gets a uniquely
named, disposable container with explicit network, filesystem, privilege, process, CPU, memory,
time, and output restrictions. Static analyzers parse the single temporary `solution.py`; they do
not import or execute it.

The same candidate, task version, test suite, tool versions, and scoring policy produce the same
result. No LLM is required or consulted.

## Why this project exists

AI-generated code needs executable, reproducible evidence rather than plausible-looking output.
CodeJudge AI begins with deterministic tests and keeps execution behind a typed runner abstraction
so stronger isolation and future analysis can evolve without moving infrastructure details into
the API or scoring core.

## Current Features

Implemented:

- FastAPI application with OpenAPI documentation at `/docs`
- Local task registry with public-safe task models
- Bundled `lru-cache` task with eight deterministic tests
- Typed `CodeRunner` abstraction with `local` and `docker` Python backends
- Restricted, non-root Docker sandbox image targeting Python 3.13
- No-network containers with memory, CPU, PID, timeout, and output limits
- Read-only root and candidate files, minimal writable storage, dropped capabilities, and
  `no-new-privileges`
- Structured test results plus syntax, testing, resource, timeout, and sandbox findings
- Explicit capability detection and HTTP `503` when the configured backend is unavailable
- Ruff code-quality findings using the documented `E`, `F`, `B`, `UP`, and `SIM` rule families
- mypy type-safety findings with a trusted configuration and no candidate plugin/config loading
- Bandit security heuristics with severity and confidence mapping
- Radon cyclomatic-complexity maximum and average metrics
- Deterministic five-dimensional weighted scoring with correctness derived only from tests
- Per-analyzer timeouts, bounded output capture, minimal environments, and temporary cleanup
- Async SQLAlchemy 2.x persistence through the PostgreSQL `asyncpg` driver
- UUID evaluation identities and append-only snapshots protected by a database trigger
- Exact source hashes, task/test fingerprints, and reproducibility fingerprints
- Stored analyzer, scoring-policy, application, and sandbox-image versions
- Historical detail and filtered/paginated summary APIs that never rerun candidate code
- Durable PostgreSQL job lifecycle and transactional outbox
- Redis Streams consumer-group delivery with acknowledgements and stale-message reclaim
- Worker leases, bounded retries, deterministic backoff, and stale-job recovery
- Idempotent terminal completion and optional HTTP `Idempotency-Key` support
- Alembic migrations plus unit, PostgreSQL, Redis, worker, and real Docker tests in CI

Planned features are listed in the roadmap and are not part of the current implementation.

## Architecture

```text
Client -> FastAPI -> PostgreSQL Job + Outbox -> Outbox Publisher -> Redis Stream
                                                                    |
                                                               Worker Pool
                                                               /         \
                                                        Docker Runner   Analysis
                                                               \         /
                                                                Score Engine
                                                                     |
                                                           Immutable Snapshot
                                                                     |
                                                                PostgreSQL
```

Execution and analysis remain deliberately sequential so infrastructure failure handling stays
simple and deterministic. The engine does not know whether execution is local or containerized,
and runners, analyzers, scoring, and snapshot construction know nothing about Redis. PostgreSQL is
the lifecycle authority; Redis is only recoverable delivery infrastructure. Routes only translate
HTTP data and never issue SQLAlchemy queries.

## Quick Start

Python 3.13 or newer, Docker, PostgreSQL 17, and Redis 7 are required for asynchronous operation.

```bash
git clone https://github.com/yunusemreyazici/codejudge-ai.git
cd codejudge-ai

uv sync --extra dev
docker compose up -d postgres redis
export PERSISTENCE_ENABLED=true
export EVALUATION_MODE=async
export DATABASE_URL=postgresql+asyncpg://codejudge:codejudge@127.0.0.1:5432/codejudge
export REDIS_URL=redis://127.0.0.1:6379/0
uv run alembic upgrade head
docker build -t codejudge-python-sandbox:phase2 sandbox/
uv run codejudge-worker
# In another terminal with the same environment:
uv run uvicorn app.main:app --reload
```

`make sandbox-build` provides the same image-build command. Open
[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for the interactive API.

## Execution Backends

Select the backend with `EXECUTION_BACKEND`.

### Docker

`docker` is the default and recommended production-oriented backend. The image is built once—not
per evaluation—and each request creates a fresh container. Implemented restrictions include:

- non-root UID/GID `10001`
- `network=none`
- hard memory and swap ceiling
- fractional CPU allocation
- PID/process ceiling
- read-only root filesystem and read-only candidate workspace
- bounded writable `/tmp` and a scoped result mount
- all Linux capabilities dropped and `no-new-privileges`
- application-enforced timeout and bounded combined output capture
- bounded per-container Docker logs
- an explicit minimal environment with no host secrets

Docker mode never falls back to local execution. If the CLI, daemon, or configured image is
unavailable, evaluation returns `503 Service Unavailable`. Docker remains a shared-kernel
container boundary; highly hostile deployments may require future gVisor, Kata Containers, or
Firecracker/microVM isolation.

Build the stable Phase 2 image tag with:

```bash
docker build -t codejudge-python-sandbox:phase2 sandbox/
```

### Local

The local runner is retained for development and ordinary tests on machines without Docker:

```bash
EXECUTION_BACKEND=local uvicorn app.main:app --reload
```

It executes candidate code as a child process with the API user's host permissions. A subprocess
and temporary directory are **not** a sandbox. Never use this backend for untrusted submissions.

## Configuration

| Variable | Default | Purpose |
| --- | ---: | --- |
| `APP_NAME` | `CodeJudge AI` | OpenAPI application name |
| `APP_ENV` | `development` | Deployment environment label |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `DEFAULT_EXECUTION_TIMEOUT` | `5.0` | Fallback timeout in task definitions |
| `MAX_CODE_SIZE` | `102400` | Maximum UTF-8 submission size in bytes |
| `EXECUTION_BACKEND` | `docker` | `docker` or explicit `local` execution |
| `SANDBOX_IMAGE` | `codejudge-python-sandbox:phase2` | Prebuilt evaluation image |
| `SANDBOX_MEMORY_MB` | `256` | Container memory and swap ceiling |
| `SANDBOX_CPUS` | `0.5` | Container CPU allocation |
| `SANDBOX_PIDS_LIMIT` | `64` | Container process ceiling |
| `SANDBOX_TIMEOUT_SECONDS` | `5.0` | Global Docker runtime ceiling |
| `SANDBOX_OUTPUT_LIMIT_BYTES` | `1048576` | Combined retained stdout/stderr bytes |
| `STATIC_ANALYSIS_ENABLED` | `true` | Enable deterministic Phase 3 analysis |
| `STATIC_ANALYSIS_TIMEOUT_SECONDS` | `5.0` | Per-analyzer process timeout |
| `STATIC_ANALYSIS_OUTPUT_LIMIT_BYTES` | `262144` | Per-analyzer combined output limit |
| `PERSISTENCE_ENABLED` | `false` | Require successful evaluations to be persisted |
| `DATABASE_URL` | unset | Required `postgresql+asyncpg://` URL when persistence is enabled |
| `EVALUATION_MODE` | `sync` | Explicit `sync` compatibility or production-oriented `async` mode |
| `REDIS_URL` | unset | Required `redis://` or `rediss://` URL in async mode |
| `WORKER_CONCURRENCY` | `1` | Concurrent worker consumer slots per process |
| `WORKER_LEASE_SECONDS` | `60` | Renewable PostgreSQL claim lease |
| `WORKER_MAX_ATTEMPTS` | `3` | Maximum infrastructure attempts |
| `OUTBOX_POLL_INTERVAL_SECONDS` | `1` | Dispatcher and maintenance interval |
| `RETRY_BASE_DELAY_SECONDS` | `5` | Backoff base; successive failed attempts yield 5, 15, then 45 seconds |

The enforced Docker timeout is the smaller of the task timeout and
`SANDBOX_TIMEOUT_SECONDS`.

## API Example

The API includes:

- `GET /health` — API process liveness
- `GET /health/sandbox` — configured execution-backend capability
- `GET /health/database` — database capability without changing liveness semantics
- `GET /health/queue` — Redis capability and TTL-backed active worker count
- `GET /api/v1/tasks` — public task specifications
- `GET /api/v1/tasks/{task_id}` — one public task
- `POST /api/v1/evaluations` — `202 queued` in async mode; synchronous compatibility otherwise
- `GET /api/v1/evaluations/{evaluation_id}` — queued/running/retry/failed state or terminal snapshot
- `GET /api/v1/evaluations` — lifecycle-aware newest-first summaries and historical snapshots

Submit the included implementation:

```bash
python -c 'import json, pathlib; print(json.dumps({
  "task_id": "lru-cache",
  "language": "python",
  "code": pathlib.Path("examples/lru_cache.py").read_text()
}))' | curl --fail-with-body \
  -H 'Content-Type: application/json' \
  --data-binary @- \
  http://127.0.0.1:8000/api/v1/evaluations
```

Unknown tasks return `404`, unsupported languages return `400`, invalid bodies return `422`, and
an unavailable configured backend returns `503`. Candidate test, syntax, timeout, OOM, and output
events return structured evaluation data rather than internal stack traces.

## Asynchronous Evaluations

With `EVALUATION_MODE=async`, POST validates the request, captures the expected source/task/test,
analyzer, scoring-policy, application, and sandbox identities, and atomically inserts a queued job
plus `evaluation.requested` outbox event. It returns `202 Accepted` immediately with the stable
evaluation UUID and status URL. It does not execute candidate code or create an in-process
background task.

The worker process contains separate outbox-publisher and consumer loops. The publisher transfers
ready outbox UUIDs to the `codejudge:evaluations` Redis Stream and marks publication only after
`XADD` succeeds. A Redis outage leaves the PostgreSQL job queued and the event unpublished for
durable retry; PostgreSQL failure causes the API to reject submission with a sanitized `503`.

Delivery is deliberately **at least once**, not exactly once. Redis consumer groups retain
unacknowledged messages, and `XAUTOCLAIM` lets another worker recover stale pending entries.
Workers atomically claim jobs in PostgreSQL, set a renewable lease, and only ACK after a durable
lifecycle transition. A duplicate delivery for a completed/failed UUID is ACKed without rerunning
candidate code.

Infrastructure failures allow three total attempts by default. The deterministic backoff function
yields 5, 15, and 45 seconds for successive failures; with the default limit, retries are scheduled
after the first two failures and the third failure is terminal. Candidate syntax errors, failed
tests, timeouts, OOM events, and ordinary findings produce completed evaluation snapshots and are
never queue retries. Expired worker leases move safely toward retry or terminal infrastructure
failure. Snapshot insertion and `running -> completed` occur in one PostgreSQL transaction, so a
crash after commit but before Redis ACK is harmless on redelivery.

Clients may send a global `Idempotency-Key` header. Replaying the same key and canonical request
returns the original UUID; reusing it for different task/language/source identity returns `409`.
Without the header, identical submissions intentionally create distinct evaluations.

Before execution, the worker hashes the exact stored source and compares the queued task version,
task/test fingerprints, analyzer versions, scoring-policy version, CodeJudge version, and expected
execution image identity with the current runtime. A mismatch becomes a non-retryable integrity
failure rather than silently running changed evaluation material. Phase 5 does not support
user-facing cancellation.

## Example Evaluation

```json
{
  "evaluation_id": "d7122434-585c-4a45-8e5d-d7f81ef96636",
  "created_at": "2026-08-27T12:00:00Z",
  "task_id": "lru-cache",
  "status": "completed",
  "score": 100.0,
  "tests": {
    "passed": 8,
    "failed": 0,
    "total": 8,
    "duration_seconds": 0.72,
    "timed_out": false
  },
  "score_breakdown": {
    "correctness": 100.0,
    "code_quality": 100.0,
    "type_safety": 100.0,
    "security": 100.0,
    "complexity": 100.0
  },
  "analysis": {
    "findings": [],
    "complexity": {
      "maximum": 3,
      "average": 2.0,
      "blocks": 4,
      "analyzable": true
    }
  },
  "findings": []
}
```

`evaluation_id` and `created_at` are present when persistence is enabled. List requests accept
`limit` (maximum 100), `offset`, `task_id`, `language`, `minimum_score`, and `maximum_score`.
Summaries intentionally omit source and findings; the detail endpoint returns the complete stored
snapshot. With persistence disabled, evaluation retains Phase 3 behavior and history/database
capability endpoints clearly report that persistence is not configured.

Top-level `findings` contains execution/test/sandbox outcomes. `analysis.findings` contains static
tool findings once, with tool, code, severity, category, location, fixability, and confidence where
the analyzer supplies them.

## Static Analysis

- **Ruff → code quality.** Runs isolated from repository/candidate config with `E`, `F`, `B`, `UP`,
  and `SIM`. `E`/`F` findings are errors, `B` warnings, and `UP`/`SIM` informational findings.
- **mypy → type safety.** Uses the packaged `app/analysis/mypy.ini`, skips followed imports and site
  packages, and does not require annotations. Candidate config and arbitrary plugins are not
  loaded.
- **Bandit → security heuristics.** Maps LOW/MEDIUM/HIGH severity and confidence into deterministic
  findings. Candidate `# nosec` suppression is ignored for scoring consistency.
- **Radon → cyclomatic complexity.** Measures functions, methods, classes, and nested closures and
  publishes maximum, average, and block count.

Each tool receives only a disposable directory containing the exact UTF-8 `solution.py`. Commands
use argument arrays rather than a shell, inherit no host environment secrets, have a five-second
default timeout, and share a bounded stdout/stderr capture per invocation. Missing tools,
timeouts, malformed/truncated output, and unexpected crashes are infrastructure failures; the API
returns `503` rather than silently awarding 100. Candidate parse errors remain ordinary findings.

Static analysis does not execute candidate source and cannot prove correctness. Bandit cannot
prove security, mypy cannot prove runtime safety, and cyclomatic complexity is only one
maintainability signal.

## Scoring Policy

Correctness remains `passed / total * 100` and is never changed by a static tool. The final score
is `correctness × 0.60 + code quality × 0.15 + type safety × 0.10 + security × 0.10 + complexity ×
0.05`, rounded to two decimals.

- Code quality starts at 100 and deducts 10/5/2 for error/warning/info findings.
- Type safety starts at 100 and deducts 8/4/0 for error/warning/info findings. Missing annotations
  are allowed by policy.
- Security deducts 25/10/3 for error/warning/info severity, multiplied by 0.5/0.75/1.0 for
  low/medium/high confidence.
- Complexity uses maximum cyclomatic complexity: 1–5 → 100, 6–10 → 90, 11–15 → 70, 16–20 → 50,
  and above 20 → 25. Source that cannot be parsed receives 0 for this dimension.

Every dimension is clamped to 0–100. Set `STATIC_ANALYSIS_ENABLED=false` only when an explicit
correctness-only legacy evaluation is desired; this omits unavailable dimensions rather than
fabricating perfect values.

## Persistence & Reproducibility

Every successfully persisted evaluation gets a fresh UUID; identical submissions are separate
historical events and are never deduplicated. One PostgreSQL row stores relational fields used for
queries and JSONB snapshots for structured analyzer versions, complexity, execution findings, and
static findings. Source is preserved byte-for-byte as text, and its SHA-256 and UTF-8 byte size
are stored alongside it.

The task fingerprint hashes canonical public task metadata plus the trusted-tests fingerprint.
The tests fingerprint hashes every regular file below the task's tests directory in sorted
relative-path order, framing both the path and exact file bytes; Python cache and bytecode files
are excluded, and absolute host paths are never included. Explicit task versions remain stored as
a separate human-managed identifier.

The reproducibility fingerprint is a canonical SHA-256 identity over the exact source hash, task
and test fingerprints, analyzer-version map, scoring-policy version, execution backend, sandbox
image tag and local image ID when available, and the CodeJudge package version. Analyzer versions
come from trusted installed-package metadata and are cached. Docker image inspection is also
cached and inability to obtain an image ID does not invalidate an otherwise trustworthy result.

A matching fingerprint means CodeJudge recorded matching evaluation inputs and allowlisted
environment metadata. It does not prove that CPU scheduling, host kernel behavior, or every source
of runtime nondeterminism was bit-for-bit identical.

Snapshots are append-only. The public API and repository expose no update or delete operation, and
a PostgreSQL trigger rejects `UPDATE` and `DELETE` as defense in depth. Historical GET requests
return stored values—including all five score dimensions—and never recompute scores or execute
candidate code. Infrastructure failures before a trustworthy result create no fake score-zero
row. When persistence is required, an atomic insert failure returns a sanitized `503` rather than
a successful response.

Alembic is the only production schema lifecycle mechanism; application startup never calls
`create_all()`:

```bash
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
```

## Security Warning

Candidate tests are not returned through the API, but they are mounted read-only in the sandbox
and can be inspected by malicious code. Phase 2 does not guarantee hidden-test confidentiality or
protect against container-runtime/kernel escapes. The API process's Docker access is itself a
high-value trust boundary, while candidate containers never receive the Docker socket.

Read [docs/security.md](docs/security.md) for the threat model, implemented controls, secret
handling, Docker daemon boundary, and remaining risks.

## Project Roadmap

- **Phase 1 — Core evaluator (implemented):** API, local runner, pytest, scoring, and findings
- **Phase 2 — Docker sandbox (implemented):** restricted containers and security tests
- **Phase 3 — Static analysis (implemented):** Ruff, mypy, Bandit, complexity, and weighted scoring
- **Phase 4 — PostgreSQL persistence (implemented):** immutable reproducible evaluation snapshots
- **Phase 5 — Redis + distributed workers (implemented):** durable at-least-once asynchronous jobs
- **Phase 6 — LLM judge + adversarial tests (planned):** complementary model review
- **Phase 7 — Multi-model benchmark + leaderboard (planned):** model comparisons
- **Phase 8 — Observability + production hardening (planned):** tracing, metrics, and operations

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -v -m "not sandbox and not database and not queue"
```

Task definitions live under `app/tasks/definitions`. Their `.yaml` files use JSON syntax, a valid
YAML subset that keeps loading on the standard library. Add execution behavior through the
`CodeRunner` protocol, never through routes or scoring.

## Testing

The lightweight suite requires no infrastructure. Database tests require an explicitly named
dedicated database ending in `_test`; Redis tests require an explicit nonzero Redis database.
Sandbox and worker E2E tests fail rather than skip when `CODEJUDGE_REQUIRE_DOCKER=1`.

```bash
# Ordinary unit and HTTP integration tests
uv run pytest -v -m "not sandbox and not database and not queue"

# PostgreSQL tests (use a dedicated database whose name ends in `_test`)
docker compose exec postgres createdb -U codejudge codejudge_test
export CODEJUDGE_TEST_DATABASE_URL=postgresql+asyncpg://codejudge:codejudge@127.0.0.1:5432/codejudge_test
DATABASE_URL="$CODEJUDGE_TEST_DATABASE_URL" uv run alembic upgrade head
uv run pytest -v -m database tests/database

# Real Redis Streams tests (use a dedicated nonzero database)
export CODEJUDGE_TEST_REDIS_URL=redis://127.0.0.1:6379/15
uv run pytest -v -m "queue and not worker_e2e" tests/queue

# Docker integration tests (build the image first)
make sandbox-build
CODEJUDGE_REQUIRE_DOCKER=1 uv run pytest -v -m sandbox tests/sandbox

# PostgreSQL + Redis + real Docker worker flow
CODEJUDGE_REQUIRE_DOCKER=1 uv run pytest -v tests/queue/test_worker_e2e.py
```

Docker tests verify successful and failing submissions, syntax errors, timeout, cleanup, network
isolation, read-only filesystem behavior, non-root identity, Docker socket absence, secret
isolation, PID limits, bounded output, and OOM detection. Candidate execution does not require
external network access.

## License

CodeJudge AI is available under the [MIT License](LICENSE).
