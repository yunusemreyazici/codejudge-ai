# Getting started

[← Project README](../README.md) · [Documentation index](README.md)

CodeJudge AI requires Python 3.13 or newer. Docker is required for the recommended execution
backend; PostgreSQL 17 and Redis 7 are required for persistence and asynchronous workers.

## Install

```bash
git clone https://github.com/yunusemreyazici/codejudge-ai.git
cd codejudge-ai
uv sync --extra dev
```

Start the repository services and build the stable sandbox image:

```bash
docker compose up -d postgres redis
docker build -t codejudge-python-sandbox:phase2 sandbox/
```

`make sandbox-build` builds the same image. The image is built once; CodeJudge creates a fresh
container for every evaluation.

## First synchronous evaluation

The default mode is synchronous and persistence is disabled. Start the API:

```bash
uv run uvicorn app.main:app --reload
```

Check liveness and sandbox capability:

```bash
curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/health/sandbox
curl --fail-with-body http://127.0.0.1:8000/api/v1/tasks
```

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

The response contains official-test counts, structured execution and analysis findings, dimension
scores, a final deterministic score, and reproducibility identity. Unknown tasks return `404`,
unsupported languages return `400`, invalid request bodies return `422`, and an unavailable
configured backend returns a sanitized `503`.

An abridged successful response has this shape:

```json
{
  "evaluation_id": "d7122434-585c-4a45-8e5d-d7f81ef96636",
  "task_id": "lru-cache",
  "status": "completed",
  "score": 100.0,
  "tests": {"passed": 8, "failed": 0, "total": 8, "timed_out": false},
  "score_breakdown": {
    "correctness": 100.0,
    "code_quality": 100.0,
    "type_safety": 100.0,
    "security": 100.0,
    "complexity": 100.0
  },
  "findings": [],
  "analysis": {"findings": []}
}
```

Top-level findings describe test, execution, and sandbox outcomes. `analysis.findings` contains
static-tool evidence. History summaries intentionally omit source and detailed findings; the detail
endpoint returns the complete stored snapshot.

## Persistent asynchronous mode

Set the durable infrastructure explicitly:

```bash
export PERSISTENCE_ENABLED=true
export EVALUATION_MODE=async
export DATABASE_URL=postgresql+asyncpg://codejudge:codejudge@127.0.0.1:5432/codejudge
export REDIS_URL=redis://127.0.0.1:6379/0
uv run alembic upgrade head
```

Run the evaluator worker and API in separate terminals with the same environment:

```bash
uv run codejudge-worker
```

```bash
uv run uvicorn app.main:app --reload
```

In async mode, `POST /api/v1/evaluations` returns `202 Accepted` with a stable evaluation ID and
status URL. Poll `GET /api/v1/evaluations/{evaluation_id}` for queued, running, retry, failed, or
terminal state. Submission does not create an in-process background task.

## Main API surface

- `GET /health`, `/health/sandbox`, `/health/database`, and `/health/queue`
- `GET /api/v1/tasks` and `GET /api/v1/tasks/{task_id}`
- `POST /api/v1/evaluations`
- `GET /api/v1/evaluations` and `GET /api/v1/evaluations/{evaluation_id}`
- `POST /api/v1/benchmarks`
- benchmark run, sample, leaderboard, and comparison endpoints under `/api/v1/benchmarks`

Interactive OpenAPI documentation is served at
[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## Local backend warning

For development without Docker, the runner can be selected explicitly:

```bash
EXECUTION_BACKEND=local uv run uvicorn app.main:app --reload
```

This runs candidate code as a child process with the API user's host permissions. A subprocess and
temporary directory are not a sandbox. Never use this backend for untrusted submissions, and note
that Docker mode never falls back to it when Docker is unavailable.

## Next steps

- Read [Architecture](architecture.md) before changing component boundaries.
- Read [Deployment](operations/deployment.md) for production-oriented configuration.
- Read [Workers](operations/workers.md) for delivery and retry semantics.
- Read [Benchmark methodology](benchmarks/methodology.md) before comparing coding models.
- Use the guarded commands in [Testing](development/testing.md), especially for migrations.
