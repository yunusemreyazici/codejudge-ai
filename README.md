# CodeJudge AI

> A production-oriented evaluation framework for testing and scoring AI-generated code.

CodeJudge AI is an open-source backend for reproducible code evaluation. Phase 1 focuses on a
small, deterministic core: versioned coding tasks, pytest-backed Python execution, structured
results, and correctness scoring through a documented FastAPI interface.

> [!CAUTION]
> **Phase 1 runs candidate programs locally in child processes. This is not a security sandbox.**
> Do not expose this version publicly or use it for arbitrary untrusted submissions. Temporary
> directories and subprocesses provide cleanup and process separation, not secure isolation.
> Container-based sandboxing is planned for Phase 2.

## Overview

The service accepts a task ID, language, and candidate source code. An evaluation engine resolves
the task and selects a language runner. The Python runner creates a temporary workspace, starts
pytest in a child process with a timeout, and returns structured counts. A dedicated scoring module
then calculates the deterministic correctness score.

The same candidate, task version, and test suite produce the same correctness result. No LLM is
required or consulted in Phase 1.

## Why this project exists

AI-generated code needs more than a plausible-looking response. It needs executable,
reproducible evidence. CodeJudge AI starts with deterministic tests and a narrow architecture that
can later gain secure sandboxing, analysis, persistence, distributed workers, and model-based
review without placing those concerns in the API or scoring core prematurely.

## Current Features

Implemented:

- FastAPI application with OpenAPI documentation at `/docs`
- Local task registry with public-safe task models
- Bundled `lru-cache` task with eight deterministic test cases
- Extensible typed runner protocol and a Python/pytest implementation
- Child-process execution, disposable workspaces, and enforced timeouts
- Structured test results and typed findings for failures, syntax/import errors, and timeouts
- Deterministic correctness scoring (`passed / total * 100`)
- UTF-8 source-size limit and non-empty submission validation
- Unit and HTTP integration tests, Ruff, mypy, and GitHub Actions CI

Planned features are listed in the roadmap and are not part of the current implementation.

## Architecture

```text
                         GET task metadata
Client ───────────────► FastAPI routes ───────────────► Task registry
  │                          │                               │
  │ POST evaluation          │ typed request                 │ task + private test path
  └─────────────────────────►│                               │
                             ▼                               │
                      Evaluation engine ◄────────────────────┘
                       │             ▲
              runner selection      │ structured RunnerResult
                       ▼             │
                  CodeRunner protocol
                       │
                       ▼
                  PythonRunner
                       │ temporary directory + timeout
                       ▼
                pytest child process
                       │
                       ▼
             findings + scoring module ──────────────► EvaluationResult
```

The engine orchestrates resolution, dispatch, and scoring. Runner implementations own execution
details. Routes translate domain errors into HTTP responses and contain no scoring or subprocess
logic. Test source and filesystem paths never appear in public task response models.

## Quick Start

Python 3.13 or newer is required.

```bash
git clone https://github.com/yunusemreyazici/codejudge-ai.git
cd codejudge-ai

python3.13 -m venv .venv
source .venv/bin/activate

python -m pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for the interactive API.

Optional environment variables:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `APP_NAME` | `CodeJudge AI` | OpenAPI application name |
| `APP_ENV` | `development` | Deployment environment label |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `DEFAULT_EXECUTION_TIMEOUT` | `5.0` | Fallback timeout for tasks without one |
| `MAX_CODE_SIZE` | `102400` | Maximum UTF-8 submission size in bytes |

## API Example

The API includes these endpoints:

- `GET /health` — process liveness
- `GET /api/v1/tasks` — list public task specifications
- `GET /api/v1/tasks/{task_id}` — retrieve one public task
- `POST /api/v1/evaluations` — synchronously evaluate a submission

Submit the included example implementation:

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

Unknown task IDs return `404`, unsupported languages return `400`, and invalid request bodies return
`422`. Test failures and candidate syntax/runtime problems normally return a structured evaluation
object rather than an internal server error.

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
    "duration_seconds": 0.31,
    "timed_out": false
  },
  "score_breakdown": {
    "correctness": 100.0
  },
  "findings": []
}
```

Only correctness is measured today. The score model deliberately does not invent code-quality,
performance, security, or LLM-review values.

## Security Warning

The Phase 1 runner writes candidate code and task tests to a temporary directory, then invokes
pytest as a subprocess. The timeout limits evaluation duration and the directory is deleted after
the run. These controls **do not restrict filesystem, network, process, or operating-system access**.
A malicious submission can act with the API process's permissions.

Do not run untrusted public submissions with this release. Phase 2 will introduce a container
sandbox with explicit resource, filesystem, network, and syscall controls.

## Project Roadmap

- **Phase 1 — Core evaluator (implemented):** API, local Python runner, pytest, deterministic
  scoring, findings, and quality gates
- **Phase 2 — Docker sandbox (planned):** secure execution boundary and resource controls
- **Phase 3 — Static analysis (planned):** typed code-quality and security findings
- **Phase 4 — PostgreSQL persistence (planned):** tasks, submissions, and evaluation history
- **Phase 5 — Redis + distributed workers (planned):** durable asynchronous workloads
- **Phase 6 — LLM judge + adversarial test generation (planned):** complementary model review
- **Phase 7 — Multi-model benchmark + leaderboard (planned):** reproducible model comparisons
- **Phase 8 — Observability + production hardening (planned):** tracing, metrics, and operations

## Development

Install the editable package with development tools, then run the quality gates:

```bash
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy app
pytest -v
```

Task definitions live under `app/tasks/definitions`. Their `.yaml` files use JSON syntax, which is
a valid YAML subset and keeps Phase 1 on the Python standard library. Each definition is validated
with Pydantic and paired with a pytest directory. Add execution behavior through the `CodeRunner`
protocol rather than the engine or routes.

## Testing

The suite covers scoring, registry validation, engine orchestration, source limits, unsupported
languages, runner success, syntax errors, timeouts, endpoint validation, task visibility, and both
correct and intentionally incorrect LRU implementations. Tests require no network services.

```bash
pytest
```

## License

CodeJudge AI is available under the [MIT License](LICENSE).
