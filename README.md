# CodeJudge AI

> A production-oriented evaluation framework for testing and scoring AI-generated code.

CodeJudge AI is an open-source backend for reproducible code evaluation. Phase 2 adds a restricted
Docker execution backend while preserving the deterministic task, scoring, finding, and FastAPI
architecture established in Phase 1.

> [!CAUTION]
> Docker materially strengthens isolation, but it is **not a perfect security boundary**. The
> Docker daemon, runtime, and host kernel remain trusted. Review the full
> [security model](docs/security.md) before handling hostile public submissions. The `local`
> backend is development-only and must never execute untrusted code.

## Overview

The service accepts a task ID, language, and candidate source code. The evaluation engine resolves
the task and dispatches it through the configured runner. In the recommended Docker mode, each
submission gets a uniquely named, disposable container with explicit network, filesystem,
privilege, process, CPU, memory, time, and output restrictions. The runner returns only structured
domain data to the engine, which computes deterministic correctness.

The same candidate, task version, and test suite produce the same correctness result. No LLM is
required or consulted.

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
- Deterministic correctness scoring (`passed / total * 100`)
- Unit, HTTP integration, and Docker security tests with GitHub Actions CI

Planned features are listed in the roadmap and are not part of the current implementation.

## Architecture

```text
Client
  │
  ▼
FastAPI routes ──────────────► task registry
  │                                │ public task + internal test path
  ▼                                │
EvaluationEngine ◄─────────────────┘
  │ runner-independent orchestration
  ▼
CodeRunner protocol
  ├── PythonRunner (explicit local development mode)
  └── DockerPythonRunner
        │ Docker CLI argument arrays
        ▼
      ┌─────────────────────────────────────┐
      │ disposable evaluation container     │
      │ non-root · network=none             │
      │ memory · CPU · PID limits           │
      │ read-only root · bounded /tmp       │
      │ cap-drop=ALL · no-new-privileges    │
      │ pytest + structured report          │
      └─────────────────────────────────────┘
        │
        ▼
RunnerResult ──► findings + deterministic scoring ──► EvaluationResult
```

The engine does not know whether execution is local or containerized. Docker lifecycle, CLI,
mount, metadata inspection, bounded stream capture, and cleanup behavior remain in the runner
infrastructure layer. Test source and host paths never appear in public task response models.

## Quick Start

Python 3.13 or newer and Docker are required for the default backend.

```bash
git clone https://github.com/yunusemreyazici/codejudge-ai.git
cd codejudge-ai

python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

docker build -t codejudge-python-sandbox:phase2 sandbox/
uvicorn app.main:app --reload
```

The committed `uv.lock` also supports `uv sync --extra dev` for reproducible development installs.

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

The enforced Docker timeout is the smaller of the task timeout and
`SANDBOX_TIMEOUT_SECONDS`.

## API Example

The API includes:

- `GET /health` — API process liveness
- `GET /health/sandbox` — configured execution-backend capability
- `GET /api/v1/tasks` — public task specifications
- `GET /api/v1/tasks/{task_id}` — one public task
- `POST /api/v1/evaluations` — synchronous evaluation

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

## Example Evaluation

```json
{
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
  "score_breakdown": {"correctness": 100.0},
  "findings": []
}
```

Only correctness is measured. Code quality, performance, type safety, security review, and LLM
review are not fabricated.

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
- **Phase 3 — Static analysis (planned):** typed quality and security findings
- **Phase 4 — PostgreSQL persistence (planned):** tasks, submissions, and evaluation history
- **Phase 5 — Redis + distributed workers (planned):** durable asynchronous workloads
- **Phase 6 — LLM judge + adversarial tests (planned):** complementary model review
- **Phase 7 — Multi-model benchmark + leaderboard (planned):** model comparisons
- **Phase 8 — Observability + production hardening (planned):** tracing, metrics, and operations

## Development

```bash
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy app
pytest -v -m "not sandbox"
```

Task definitions live under `app/tasks/definitions`. Their `.yaml` files use JSON syntax, a valid
YAML subset that keeps loading on the standard library. Add execution behavior through the
`CodeRunner` protocol, never through routes or scoring.

## Testing

The ordinary suite does not require Docker. Sandbox tests skip with an explicit capability reason
when the daemon or image is unavailable.

```bash
# Ordinary unit and HTTP integration tests
pytest -v -m "not sandbox"

# Docker integration tests (build the image first)
make sandbox-build
pytest -v -m sandbox tests/sandbox
```

Docker tests verify successful and failing submissions, syntax errors, timeout, cleanup, network
isolation, read-only filesystem behavior, non-root identity, Docker socket absence, secret
isolation, PID limits, bounded output, and OOM detection. Candidate execution does not require
external network access.

## License

CodeJudge AI is available under the [MIT License](LICENSE).
