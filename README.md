# CodeJudge AI

> A distributed evaluation and benchmarking platform for AI-generated code.

CodeJudge AI executes generated Python in resource-limited Docker containers, combines official
tests with Ruff, mypy, Bandit, Radon, and optional AI-assisted review, and stores immutable
PostgreSQL provenance. Durable Redis workers can evaluate submissions or compare coding models
across versioned benchmark datasets without mixing AI opinions into deterministic scores.

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

The deterministic score remains defined entirely by the candidate, trusted task/tests, tool
versions, and deterministic scoring policy. AI assessment is optional, separately identified,
and may be unavailable without invalidating a deterministic result.

## Why this project exists

AI-generated code needs executable, reproducible evidence rather than plausible-looking output.
CodeJudge AI begins with deterministic tests and keeps execution behind a typed runner abstraction
so stronger isolation and future analysis can evolve without moving infrastructure details into
the API or scoring core.

## What it demonstrates

### Secure execution

- Non-root, no-network, read-only Docker evaluation with CPU, memory, PID, time, and output limits
- Dropped capabilities, `no-new-privileges`, scoped writable storage, and mandatory cleanup

### Deterministic evaluation

- Versioned public tasks, official pytest suites, trusted references, and stable fingerprints
- Ruff, mypy, Bandit, and Radon findings with a fixed five-dimensional scoring policy

### AI-assisted evaluation

- Versioned structured judge prompts and reference-validated adversarial tests
- Separate AI score, provenance, coverage, and disagreement—never blended into deterministic rank

### Distributed processing

- FastAPI, PostgreSQL, Redis Streams, transactional outboxes, leases, retries, and idempotent workers
- At-least-once delivery with PostgreSQL—not Redis—as lifecycle authority

### Reproducibility

- Immutable snapshots containing exact source, task/test/analyzer/scoring/sandbox identities
- Historical reads and aggregates that never rerun source or contact a provider

### Benchmarking

- Repeated multi-model runs over immutable datasets with public-only coding prompts
- Deterministic leaderboards alongside coverage, per-task results, latency, tokens, and cost snapshots

## Architecture

```text
Client -> FastAPI -> PostgreSQL Job + Outbox -> Redis Stream -> Worker
                                                                |
                         +--------------------------------------+-------------------+
                         |                                                          |
              Deterministic Pipeline                                        AI Pipeline
              Docker + analyzers                               Judge + adversarial generator
                         |                                             |            |
              Deterministic Score                              strict schemas       v
                         |                                              reference Docker
                         |                                              candidate Docker
                         +--------------------------------------+-------------------+
                                                                |
                                                Immutable deterministic + AI snapshot
                                                                |
                                                           PostgreSQL
```

Execution and analysis remain deliberately sequential so infrastructure failure handling stays
simple and deterministic. The engine does not know whether execution is local or containerized,
and runners, analyzers, scoring, and snapshot construction know nothing about Redis. PostgreSQL is
the lifecycle authority; Redis is only recoverable delivery infrastructure. Routes only translate
HTTP data and never issue SQLAlchemy queries.

Benchmark runs add a parallel orchestration path:

```text
Coding Model -> Generated Candidate -> Benchmark Worker -> CodeJudge Evaluation
                                                        |-> Docker official tests
                                                        |-> Ruff / mypy / Bandit / Radon
                                                        `-> optional AI judge + adversarial tests
                                                                  |
                                                         Immutable Snapshot
                                                                  |
                                                      Aggregation + Leaderboard
```

The coding provider sees only the public task specification. Generated source is never executed
outside the existing runner, and benchmark aggregation never reruns providers or evaluations.

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
# In another worker process when BENCHMARK_ENABLED=true and BENCHMARK_CONFIG is set:
uv run codejudge-benchmark-worker
# In another terminal with the same environment:
uv run uvicorn app.main:app --reload
```

Set `BENCHMARK_ENABLED=true` plus the benchmark provider variables documented below before starting
the benchmark worker. `make sandbox-build` provides the same image-build command. Open
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
- bounded writable `/tmp`; the candidate workspace contains only `solution.py`
- zero candidate capabilities and `no-new-privileges`; only the supervisor retains
  `SETUID`/`SETGID` long enough to create the non-root candidate process
- host-side private assertions over a bounded, one-operation-at-a-time JSON-lines protocol
- application-enforced timeout and bounded combined output capture
- bounded per-container Docker logs
- an explicit minimal environment with no host secrets

Docker mode never falls back to local execution. If the CLI, daemon, or configured image is
unavailable, evaluation returns `503 Service Unavailable`. Docker remains a shared-kernel
container boundary; highly hostile deployments may require future gVisor, Kata Containers, or
Firecracker/microVM isolation.

Capability checks use a bounded 10-second command timeout. Transient daemon responses, timeouts,
and malformed empty probe responses are retried at most three times with short deterministic
delays; a definitively missing sandbox image is not retried. Failure responses include a safe
reason code without exposing Docker host configuration. OOM classification requires either
Docker's inspected `OOMKilled` state or a bounded `oom` event whose actor exactly matches the
evaluation container; exit status 137 alone is never treated as OOM evidence.

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
| `LLM_ENABLED` | `false` | Enable persisted supplemental AI assessment; requires persistence |
| `LLM_BASE_URL` | unset | OpenAI-compatible API base URL; never persisted |
| `LLM_API_KEY` | unset | Provider secret; never persisted or logged |
| `LLM_PROVIDER_ID` | `default-openai-compatible` | Non-secret logical provider identity |
| `LLM_JUDGE_MODEL(S)` | unset | One model or comma-separated optional judge panel |
| `LLM_ADVERSARIAL_MODEL` | unset | Structured adversarial-test generator model |
| `LLM_TIMEOUT_SECONDS` | `30` | Hard timeout for each provider attempt |
| `LLM_MAX_ATTEMPTS` | `2` | Provider transient attempts; never whole-job retries |
| `LLM_MAX_OUTPUT_TOKENS` | `2000` | Explicit provider generation ceiling |
| `LLM_MAX_INPUT_BYTES` | `100000` | Skip AI instead of silently truncating input |
| `LLM_MAX_RESPONSE_BYTES` | `262144` | HTTP response cap before parsing |
| `LLM_MAX_ADVERSARIAL_TESTS` | `5` | Generated-test count ceiling |
| `BENCHMARK_ENABLED` | `false` | Enable benchmark API planning and worker configuration |
| `BENCHMARK_CONFIG` | unset | Phase 7.2 YAML used by the benchmark worker to resolve provider env names |
| `BENCHMARK_BASE_URL` | unset | Legacy single OpenAI-compatible coding provider base URL; never persisted |
| `BENCHMARK_API_KEY` | unset | Coding provider credential; never persisted or logged |
| `BENCHMARK_PROVIDER_ID` | `default-benchmark-openai-compatible` | Generation provider identity |
| `BENCHMARK_GENERATION_CONCURRENCY` | `2` | Conservative generation worker concurrency |
| `MAX_BENCHMARK_MODELS` | `5` | Model configurations allowed per run |
| `MAX_BENCHMARK_TASKS` | `10` | Dataset tasks allowed per run |
| `MAX_BENCHMARK_SAMPLES_PER_TASK` | `10` | Repeated samples allowed per model/task |
| `MAX_BENCHMARK_TOTAL_GENERATIONS` | `100` | Planned generation ceiling per run |

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
- `POST /api/v1/benchmarks` — durably plan a versioned comparison run and return `202`
- `GET /api/v1/benchmarks/{run_id}` — run lifecycle, identity, and configuration summary
- `GET /api/v1/benchmarks/{run_id}/samples` — paginated/filterable sample summaries
- `GET /api/v1/benchmarks/{run_id}/samples/{sample_id}` — artifact provenance and evaluation link
- `GET /api/v1/benchmarks/{run_id}/leaderboard` — deterministic and per-task metrics
- `POST /api/v1/benchmarks/compare` — compatibility-checked cross-run comparison

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

## AI-Assisted Evaluation

AI evaluation is supplemental. The existing top-level `score` and every field in
`score_breakdown` retain their Phase 3 meaning and cannot be modified by judge output or generated
tests. Phase 6 stores a separate `ai_assessment` with `disabled`, `completed`, `partial`,
`unavailable`, `disputed`, or `skipped` status and an optional `ai_score`.

The judge receives only the public task, exact candidate source as explicitly untrusted JSON data,
and structured deterministic evidence. It never receives hidden tests, the reference solution,
infrastructure details, credentials, or tools. Strict local schemas reject malformed scores,
unknown fields, and oversized findings. Impossible optional source lines are removed. CodeJudge,
not the model, computes judge scores from versioned dimensions. Optional panels store every judge,
use the median, and suppress `ai_score` when the score spread exceeds the configured threshold.

Adversarial evaluation follows a bounded pipeline:

```text
generate -> structural policy validation -> trusted reference Docker run -> candidate Docker run
```

Generated tests are AI-produced, not official hidden tests. They are deduplicated and checked for
syntax, naming, size, plugin declarations, and prohibited imports, but this validator is a quality
layer rather than the security boundary. Test code runs only in the same restricted Docker model
used for candidate execution. A test counts only after passing the private packaged reference
implementation; zero valid tests produces no robustness score. Generated tests never change
deterministic correctness or the top-level score.

AI provider timeouts, rate limits, selected server errors, malformed output, refusals, or generator
failures do not retry or fail a completed deterministic evaluation. They produce partial or
unavailable AI metadata and the worker commits the deterministic snapshot normally. If queued AI
prompt/model/policy/reference identity differs at execution, deterministic evaluation proceeds and
AI is recorded as `skipped` with `ai_identity_mismatch`.

Candidate source can contain natural-language prompt injection. CodeJudge keeps it out of system
instructions, serializes it under an untrusted-data field, locally validates every response, gives
the model no tools, and provides no path for AI results to mutate deterministic evidence. These
controls limit impact but do not prove that model-level prompt injection is completely solved.

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

The AI score is separate: when both components are valid, policy version `1` computes
`judge_score × 0.70 + adversarial_robustness × 0.30`. Missing or disputed components produce no
aggregate AI score; weights are never renormalized. **Deterministic score ≠ AI score.**

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

Phase 6 adds a separate AI reproducibility fingerprint over the deterministic fingerprint,
AI-policy and prompt hashes, logical provider/model identities, non-secret generation parameters,
panel identity, response hashes, and trusted-reference fingerprint. Per-call artifacts retain
model, prompt version/hash, rendered-input hash, token usage where supplied, latency, provider
response ID, and normalized result. API keys and credential-bearing URLs are excluded. Matching AI
fingerprints improve explainability but do not guarantee identical future output: hosted models
can be nondeterministic and providers can change their implementation.

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

## Benchmarks

A run expands a repository-versioned dataset across ordered model configurations and repeated
samples. Each coding model receives the same versioned public-only prompt, and each valid generated
source enters the ordinary CodeJudge Docker/analyzer/snapshot pipeline. The initial
`codejudge-core@1` remains the immutable original LRU-only dataset. `codejudge-core@2` contains a
seven-task engineering portfolio with equal weights:

| Task | Primary skill |
| --- | --- |
| LRU Cache | data structures and recency state |
| TTL Cache | expiration, state, and eviction |
| Sliding-Window Rate Limiter | time-window algorithms and per-key state |
| Retry Backoff | deterministic reliability arithmetic |
| Dependency Resolver | graphs, cycles, and stable ordering |
| Async Batch Processor | asyncio, bounded concurrency, and cleanup |
| Circuit Breaker | explicit reliability state machines |

See [Benchmark Design](docs/BENCHMARK_DESIGN.md) for contracts, selection rationale, common bugs,
determinism, privacy, and interpretation limits.

The primary leaderboard rule is higher weighted mean deterministic score, then higher coverage,
then higher deterministic median, with the model-configuration fingerprint as the stable final
tie-breaker. Pass rate means `deterministic score == 100`. Mean, median, sample standard deviation,
minimum, maximum, per-task metrics, and nearest-rank p95 latency are reported without claiming
statistical significance from small sample counts.

Score and coverage must be read together. A refusal, provider failure, malformed response, or
evaluation infrastructure failure has a null deterministic score and is never silently converted
to zero. AI score, judge score, adversarial robustness, disputes, and AI coverage remain separate
supplemental columns and never affect rank.

Generation pricing is an explicit provider/model/version snapshot. Token usage and calculated cost
are persisted with the artifact, so later pricing changes cannot rewrite history. Unknown pricing
is null—not free—and currencies are totaled separately without automatic conversion.

Comparable runs must share the dataset fingerprint, coding-prompt version and hash, deterministic
evaluator fingerprint, samples per task, and benchmark-policy version. Reproducibility metadata
also includes model parameters, sandbox/analyzer/scoring identity, pricing version, generation
attempt count, and exact generated-source hashes. Provider seeds and hashes improve provenance but
cannot make an externally hosted model perfectly reproducible.

Leaderboard results are conditional on dataset selection, public prompt wording, model parameters,
sampling randomness, provider backend/version changes, rate limits, test quality, and any configured
judge-model bias. They are controlled comparisons, not universal intelligence rankings.

Create a v2 run:

```bash
curl --fail-with-body -H 'Content-Type: application/json' \
  --data '{
    "dataset_id": "codejudge-core",
    "dataset_version": "2",
    "models": [
      {"provider_id": "configured-provider", "model": "coding-model", "temperature": 0}
    ],
    "samples_per_task": 1
  }' http://127.0.0.1:8000/api/v1/benchmarks
```

`GET /api/v1/benchmarks/{run_id}/leaderboard` reports deterministic mean/median, coverage,
per-task scores, separate AI coverage/score, generation latency, token usage, and snapshotted cost.
The CI fake-model run demonstrates this contract; it is not a real-model performance claim.

### Controlled real-model workflow

Phase 7.2 adds a commit-safe YAML format and one `codejudge-benchmark` CLI. Configuration stores
logical provider IDs, model parameters, pricing snapshots, and the *names* of endpoint/credential
environment variables. It never stores their values. The only supported real generation protocol
is OpenAI-compatible chat completions; providers with a different protocol are unsupported until a
truthful adapter is implemented.

Start with a copy of
[`benchmark-configs/real-smoke.example.yaml`](benchmark-configs/real-smoke.example.yaml), replace
the placeholder provider/model identities and example pricing with reviewed values, then export the
named endpoint and credential variables. Planning is provider-free and uses a conservative input
bound plus each model's configured maximum output tokens:

```bash
uv run codejudge-benchmark plan benchmark-configs/real-smoke.yaml
```

Unknown pricing is displayed as `unknown`, never zero. A configured USD budget is rejected if the
known maximum estimate exceeds it, if any model price is unknown, or if another currency would
require conversion. The estimate is not actual spend. Actual provider-reported tokens and cost
calculated from the immutable pricing snapshot are stored after generation.

For a smoke run, use two models, seven v2 tasks, and one sample per task: 14 generations. Verify
credentials, structured source output, Docker evaluation, failures, token/cost coverage, candidates,
and provenance before creating a full configuration. A typical full comparison uses three models,
seven tasks, and three samples per task (63 generations). Repeated samples expose hosted-model
nondeterminism; the values remain user-controlled and are not hard-coded. Keep AI evaluation
disabled for the primary comparison unless a separate, explicitly priced judge experiment is
intended.

The exact operational flow is:

1. Start PostgreSQL and Redis: `docker compose up -d postgres redis`.
2. Set `PERSISTENCE_ENABLED=true`, `DATABASE_URL`, and `REDIS_URL`.
3. Apply migrations with `uv run alembic upgrade head`.
4. Build the sandbox with `docker build -t codejudge-python-sandbox:phase2 sandbox/`.
5. Start `codejudge-worker` only if ordinary asynchronous evaluations are also needed.
6. Set `BENCHMARK_ENABLED=true` and `BENCHMARK_CONFIG=benchmark-configs/real-smoke.yaml`, then
   start `uv run codejudge-benchmark-worker`.
7. Export only the endpoint and credential variables named by the YAML; never print them.
8. Run `uv run codejudge-benchmark plan benchmark-configs/real-smoke.yaml` and review every warning.
9. Explicitly accept paid execution with
   `uv run codejudge-benchmark run benchmark-configs/real-smoke.yaml`.
10. Inspect with `uv run codejudge-benchmark status <RUN_ID>`.
11. Generate artifacts with `uv run codejudge-benchmark report <RUN_ID>`.
12. Review `benchmark-results/generated/<RUN_ID>/` before deliberately copying measured artifacts
    into `benchmark-results/published/<name>/`.

`run` prints the complete plan before durably queuing samples; it is the explicit execution
boundary and has no interactive confirmation. It returns immediately by default because the
benchmark worker owns durable asynchronous execution; `--wait` is available for convenience.
`export` writes canonical `results.json` plus opaque UUID-named candidate files. `report` also
writes `report.md`, whose metadata identifies the exact results JSON SHA-256. Source hashes are
recomputed byte-for-byte before export. A non-terminal run requires `--allow-incomplete`; a failed
run gets diagnostic provenance but no misleading leaderboard.

Normal output under `benchmark-results/generated/` is Git-ignored. CodeJudge never calls providers
during tests or CI, never publishes or commits results, and never updates this README with a result.
See [`benchmark-results/README.md`](benchmark-results/README.md) for the human-review convention.

## Security Warning

Official tests and trusted references are not mounted in candidate workspaces. A trusted host-side
harness sends one bounded public-API operation at a time to a stateful candidate process and keeps
assertions, expected values, future operations, and evaluator paths outside the candidate
interpreter. Real-Docker canary tests enforce this boundary. Docker still does not protect against
container-runtime/kernel escapes; daemon access remains a high-value trust boundary, and candidate
containers never receive the Docker socket.

Read [docs/security.md](docs/security.md) for the threat model, implemented controls, secret
handling, Docker daemon boundary, and remaining risks.

## Project Roadmap

- **Phase 1 — Core evaluator (implemented):** API, local runner, pytest, scoring, and findings
- **Phase 2 — Docker sandbox (implemented):** restricted containers and security tests
- **Phase 3 — Static analysis (implemented):** Ruff, mypy, Bandit, complexity, and weighted scoring
- **Phase 4 — PostgreSQL persistence (implemented):** immutable reproducible evaluation snapshots
- **Phase 5 — Redis + distributed workers (implemented):** durable at-least-once asynchronous jobs
- **Phase 6 — LLM judge + adversarial tests (implemented):** versioned review and validated tests
- **Phase 7 — Multi-model benchmark + leaderboard (implemented):** durable controlled comparisons
- **Phase 7.1 — Dataset expansion (implemented):** seven rigorous, diverse engineering tasks
- **Phase 7.2 — Real benchmark workflow (implemented):** safe planning, export, and reporting
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

# Phase 6 contracts and fake-LLM real-Docker worker E2E
uv run pytest -v -m ai tests/ai
CODEJUDGE_REQUIRE_DOCKER=1 uv run pytest -v tests/queue/test_ai_worker_e2e.py

# PostgreSQL + Redis + real Docker worker flow
CODEJUDGE_REQUIRE_DOCKER=1 uv run pytest -v tests/queue/test_worker_e2e.py

# Phase 7 fake-model + PostgreSQL + Redis + real Docker benchmark flow
CODEJUDGE_REQUIRE_DOCKER=1 uv run pytest -v tests/queue/test_benchmark_worker_e2e.py

# Authoritative full verification with all services configured
CODEJUDGE_REQUIRE_DOCKER=1 uv run pytest -v
```

Docker tests verify successful and failing submissions, syntax errors, timeout, cleanup, network
isolation, read-only filesystem behavior, non-root identity, Docker socket absence, secret
isolation, PID limits, bounded output, and OOM detection. Candidate execution does not require
external network access.

## License

CodeJudge AI is available under the [MIT License](LICENSE).
