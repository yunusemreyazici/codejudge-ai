# CodeJudge AI documentation

The root [README](../README.md) is the project landing page. This index routes operators,
contributors, benchmark reviewers, and security reviewers to the detailed contract they need.

## Start here

| Document | Audience | What it covers |
| --- | --- | --- |
| [Getting started](getting-started.md) | New users | Installation, first API run, Docker image, and async prerequisites |
| [Architecture](architecture.md) | Contributors and operators | Component boundaries, durable flows, trust boundaries, and failure ownership |

## Evaluation

| Document | What it covers |
| --- | --- |
| [Scoring](evaluation/scoring.md) | Versioned deterministic dimensions, weights, and AI separation |
| [Correctness](evaluation/correctness.md) | Official-test authority, test-count integrity, and failure semantics |
| [Static analysis](evaluation/static-analysis.md) | Ruff, mypy, Bandit, Radon, exact-source handling, and failure policy |

## Sandbox

| Document | What it covers |
| --- | --- |
| [Security model](sandbox/security-model.md) | Threat model, implemented controls, trusted components, and residual risk |
| [Resource limits](sandbox/resource-limits.md) | CPU, memory/swap, PIDs, time, output, cleanup, timeout, and OOM evidence |
| [Legacy security entry point](security.md) | Compatibility link for older references |

## Benchmarking

| Document | What it covers |
| --- | --- |
| [Methodology](benchmarks/methodology.md) | End-to-end benchmark flow, ranking, coverage, and interpretation |
| [Datasets](benchmarks/datasets.md) | Immutable dataset identities, versioned task portfolios, hidden tests, and prompts |
| [Task quality](benchmarks/task-quality.md) | Mutation discrimination, portfolio balance, runtime density, and known gaps |
| [Historical results](benchmarks/results.md) | Preserved real-model panels with run-level provenance and caveats |
| [Statistics](benchmarks/statistics.md) | Task-first means, repeated samples, intervals, dispersion, and stability |
| [Providers and budgets](benchmarks/providers.md) | Output modes, capability config, concurrency, secrets, pricing, and preflight |
| [Reproducibility](benchmarks/reproducibility.md) | Fingerprints, compatible comparisons, exports, archives, and offline checks |
| [Original benchmark design note](BENCHMARK_DESIGN.md) | Dataset-selection rationale and interpretation limits retained from Phase 7.1 |

## Operations

| Document | What it covers |
| --- | --- |
| [Database](operations/database.md) | PostgreSQL authority, migrations, immutable snapshots, and test-DB safety |
| [Workers](operations/workers.md) | Redis delivery, outboxes, leases, retries, identity checks, and recovery |
| [Deployment](operations/deployment.md) | Environment, health endpoints, startup order, sandbox preflight, and secrets |

## Development and release

| Document | What it covers |
| --- | --- |
| [Testing](development/testing.md) | Lightweight, database, Redis, sandbox, E2E, and guarded full-suite commands |
| [Release process](development/release-process.md) | Change control, version sources, verification, tags, and artifact safety |

## Documentation principles

- Document behavior from source, tests, workflow configuration, or immutable stored evidence.
- Keep deterministic scores separate from optional AI assessment.
- Name missing observations as missing; do not convert them to zero unless a metric explicitly
  defines that penalty.
- Distinguish observed leaderboard leaders from fully eligible winners.
- Identify the execution-time CodeJudge version for historical evidence. Archive-creation runtime
  is separate metadata and may be newer.
- Never put provider endpoints, credentials, candidate secrets, hidden tests, or reference source
  into public examples.
