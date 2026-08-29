# CodeJudge AI Security Model

## Scope and threat model

Phase 7.1 assumes submitted Python and all LLM output are actively malicious. A candidate may loop
forever, allocate memory, emit unlimited output, spawn processes, inspect its environment, write
files, access task tests, attempt network connections, or probe host resources.

The Docker backend materially reduces exposure from these actions by running each evaluation in a
new, restricted container. Docker is not a perfect security boundary. The Docker daemon, container
runtime, host-side official-test harness, and host kernel remain trusted. Kernel or runtime escapes,
denial of service against the Docker daemon, side channels, and vulnerabilities in Python are
outside the implemented protection.

Static analysis is a separate process-only path and does not weaken the Docker boundary. Ruff,
mypy, Bandit, and Radon receive a disposable directory containing only the exact `solution.py`.
The API process never imports, evaluates, or executes candidate source for analysis. Analyzer
commands use fixed argument arrays, a minimal non-secret environment, explicit per-tool timeouts,
and bounded combined output capture. The mypy adapter uses a packaged configuration, disables
site-package discovery and followed imports, and never loads candidate configuration or plugins.
Analyzer processes remain trusted dependencies; parser vulnerabilities in those tools are a
remaining risk and should be addressed through dependency updates and stronger process isolation
in production deployments.

Systems evaluating highly hostile public code should consider stronger isolation layers such as
gVisor, Kata Containers, or microVMs such as Firecracker in addition to the controls implemented
here.

## Implemented restrictions

Every Docker evaluation receives a unique name and labels. Official evaluations contain a small
root supervisor whose only privileged operation is dropping a child process to the candidate
identity. Candidate code itself runs with:

- UID and GID `10001`, with zero effective Linux capabilities
- `--network none`
- hard memory and memory-swap ceilings
- a fractional CPU allocation
- a PID limit
- a read-only root filesystem
- all capabilities dropped except `SETUID`/`SETGID` on the supervisor so it can create the
  candidate-UID child; the child loses those capabilities during the UID transition
- `no-new-privileges`
- a bounded writable `/tmp` tmpfs with `noexec`, `nosuid`, and `nodev`
- a read-only bind mount containing only `solution.py`
- a bounded JSON-lines protocol over the attached container's stdin/stdout
- an application-level execution timeout
- bounded combined stdout/stderr capture that continues draining discarded bytes
- a bounded per-container Docker log using the local log driver

The runner never uses privileged mode, host networking, the host root filesystem, home or SSH
directories, or `/var/run/docker.sock`. Docker commands are argument arrays and never shell-built.
The only general-purpose writable filesystem is the size-bounded `/tmp` tmpfs. Containers are
force-removed in a `finally` block after success, failure, malformed protocol responses, OOM,
timeout, and cancellation.

OOM results require runtime evidence: either the container's inspected `OOMKilled` state or a
Docker `oom` event queried before cleanup with exact container identity and bounded execution
timestamps. Because daemon event publication may trail container wait/inspect completion briefly,
an empty successful event query is retried five times over a bounded 500 ms backoff window before
classification. Exit status 137 alone is not sufficient because non-OOM `SIGKILL` paths share it.
Docker capability commands have a 10-second bound and at most three short retries for transient
daemon/probe failures; candidate execution itself is never retried or moved to a local fallback.

## Secrets and environment

The host application's environment is not forwarded. The runner passes only:

- `PYTHONDONTWRITEBYTECODE=1`
- `PYTHONUNBUFFERED=1`
- the fixed candidate import path (`PYTHONPATH=/workspace`)

The image itself may define ordinary Python runtime variables. API keys, cloud credentials,
database URLs, CI tokens, GitHub tokens, and SSH variables from the API process are not copied into
the container. Do not put secrets in the sandbox image or candidate mounts.

## Docker daemon trust boundary

The CodeJudge API must be permitted to call the host Docker daemon, which is a highly privileged
service. Candidate containers never receive the Docker socket or Docker credentials. Operators
must protect daemon access, keep Docker and the host kernel patched, restrict who can change the
sandbox image, and monitor containers carrying the `codejudge.component=sandbox` label.

## Official-test privacy and result integrity

Official deterministic tests execute as trusted host-side case plans. Their source, expected
values, case names, canaries, reference implementations, and repository paths are never mounted or
copied into the candidate container. The host sends one schema-bounded invocation at a time to a
root supervisor. That supervisor starts a fresh UID/GID `10001` candidate interpreter per test
case and forwards only the current operation. It never forwards future operations or expected
results. Stateful sequences remain in that candidate process, while cases are isolated from one
another.

The candidate-visible runtime consists of `solution.py`, the generic baked-in invocation worker,
the Python runtime, and normal base-image files. The worker can construct public classes, invoke
functions/methods, await returned awaitables, preserve object identity within a case, create a
small allowlisted set of callback/async-worker probes, and return JSON-safe results or exception
type/message data. It cannot select host paths or commands. Private assertions remain on the host.

Real-Docker regression tests place stable private canaries in host-only official-test and reference
files, then run a malicious candidate that traverses readable `/workspace`, `/tmp`, `/app`, runtime
paths, and accessible `/proc` metadata. The test repeats three times and requires a minimal
workspace, no private filenames, no canary content, candidate UID/GID `10001`, and zero effective
capabilities.

The development-only local backend still copies pytest files into a local child workspace and is
not a privacy or security boundary; it must never execute untrusted code. The hidden-test guarantee
applies to the Docker backend used by workers and benchmarks.

## Persisted source and database boundary

Phase 4 and Phase 5 store exact submitted source in PostgreSQL so a historical evaluation remains
explainable. Full source is omitted from list responses but deliberately returned by the UUID
detail endpoint. Authentication and tenant authorization are outside Phase 5, so operators must
not expose this API to mutually untrusted users without an access-control layer.

The application does not log submitted source, database URLs, SQL statements, or candidate
environment data. Persisted execution metadata is allowlisted to the backend, sandbox image tag,
and local image identity. Host environment secrets are neither copied to sandbox containers nor
stored in evaluation snapshots. Database exceptions are logged as typed infrastructure failures
without connection details and are returned to clients as sanitized `503` responses.

Evaluation rows are append-only: there are no update/delete API or repository methods, and a
database trigger rejects row updates and deletes. Database administrators and schema migrations
remain trusted and can bypass or replace this policy, so database credentials and migration access
must be protected separately from ordinary application access.

## Queue and worker boundary

PostgreSQL is the authoritative store for submitted source, expected runtime identity, lifecycle,
retry state, and terminal snapshots. Redis Streams is an at-least-once delivery mechanism and
contains only evaluation UUIDs. Candidate source, database/Redis credentials, Docker arguments,
host paths, analyzer commands, and scoring configuration are never accepted from queue fields.
Workers resolve all execution material from trusted PostgreSQL state and the packaged registry.

The transactional outbox prevents an accepted PostgreSQL job from being silently lost when Redis
is unavailable. Duplicate publication and redelivery are expected: PostgreSQL row locking, leases,
stable evaluation UUIDs, and atomic snapshot/job completion prevent contradictory terminal
results. Worker logs contain evaluation identity, worker identity, attempts, safe error codes, and
exception classes only; they must not contain source or connection URLs.

An unauthenticated global `Idempotency-Key` namespace is intentionally simple for Phase 5. It is
not tenant isolation and can expose whether a key was previously used. Worker heartbeat keys in
Redis contain random runtime identities and expire automatically; no host secrets are stored in
them.

## AI provider and generated-test boundary

The LLM is not trusted. It receives bounded structured inference requests and no shell, Docker,
database, Redis, repository, browsing, or other tool access. Candidate source is serialized only
inside an explicitly untrusted input object and never interpolated into system instructions.
Provider output is size-bounded and validated against strict local schemas. Raw prompts, candidate
source, API keys, credential-bearing base URLs, authorization headers, and raw exceptions are not
logged or persisted. Only a logical provider ID and non-secret provenance are stored.

Generated tests are also untrusted. Deterministic structural checks reject malformed, oversized,
duplicate, plugin-declaring, or prohibited test structures, but these checks are not the security
boundary. Each generated pytest runs in a separate non-root, no-network, read-only,
resource-limited Docker invocation—first against a workspace containing only the trusted reference
and that generated test, and only then against a distinct workspace containing only the candidate
and that generated test. Candidate execution never shares a workspace or process with the trusted
reference. Generated tests never execute in FastAPI, a worker process, or directly on the host.

Unlike official deterministic tests, an adversarial test is generated model output and must be
present in the candidate's generated-test sandbox so pytest can execute it. It is therefore
readable by that candidate. This does not expose official tests, private canaries, or references;
it remains an explicit limitation of the supplemental Phase 6 robustness signal.

The reference solution is private evaluator material. It is packaged for worker use but is never
returned by task/evaluation APIs, included in an LLM request, written to Redis, or logged. Passing
the reference is a strong guardrail against invalid generated tests; it is not formal proof that a
test fully represents the public specification.

This rule applies uniformly to every `codejudge-core@2` task. Portfolio privacy tests enumerate the
dataset and confirm neither reference bytes nor evaluator paths appear in task listings/details or
coding-provider payloads. Function-based, class-based, time-dependent, and async task references
all use the same discovery and Docker adversarial-validation path.

Prompt injection remains possible at the model reasoning layer. Phase 6 limits its impact by
keeping deterministic scoring authoritative, separating AI findings and provenance, validating
structured output, denying model tool access, preventing AI mutation of deterministic fields, and
reference-validating generated tests. CodeJudge does not claim complete prompt-injection
prevention.

## Benchmark generation boundary

The coding-generation provider receives only public task identity, title, description, language,
entrypoint, timeout, and the configured output contract. `structured_json` uses strict JSON Schema;
benchmark-only `raw_source` treats non-empty assistant content as exact untrusted candidate bytes
without cleanup. Phase 6 judge and adversarial-generation traffic remains strict structured JSON.
Dataset files contain task/test fingerprints, not test source or reference implementations. API
keys, Docker/Redis/database
metadata, hidden tests, and reference solutions are never included in generation requests or
benchmark provenance.

Generated source is size-bounded, hashed byte-for-byte without normalization, persisted before
evaluation, and treated as untrusted candidate input. It always enters the existing Docker sandbox
and analysis pipeline. Redelivery resumes from a durable artifact instead of calling the provider
again, while query and leaderboard endpoints neither execute source nor contact providers.
Generated comments and strings remain untrusted input to the separately configured Phase 6 judge,
preserving the existing prompt-injection separation.

The benchmark `probe` command makes exactly one provider request and does not create a run. Its
default output is limited to status, normalized envelope shape, content type/length, usage
presence, latency, finish reason, and provider response model. Raw content is printed only with the
explicit `--show-content` diagnostic option; secrets and prompts are never printed.

## Remaining risks

- Container and kernel escape vulnerabilities
- Host resource pressure below or outside the configured cgroup controls, including Docker daemon
  overhead and temporary container logs
- Side channels between workloads sharing a kernel
- Malicious behavior against Python, generated-test pytest, or trusted invocation components
- Candidate visibility into the specific AI-generated adversarial test being run against it
- Host I/O pressure up to the bounded container log and timeout ceilings
- Local backend execution, which has no security boundary and must not receive untrusted code
- Exposure of stored candidate source when the unauthenticated Phase 5 API is deployed without an
  external authorization layer
- Loss or compromise of PostgreSQL/Redis data or credentials; Phase 5 does not add encryption at rest or
  tenant isolation
- No user-facing cancellation; an accepted job proceeds until it reaches a terminal lifecycle
  state
- Nondeterministic or provider-version-dependent LLM behavior even when recorded AI fingerprints
  match
- Model-level prompt injection, hallucinated reasoning, and biased assessment within the isolated
  supplemental AI result

Report security issues privately to the repository maintainers rather than opening a public issue
with exploit details.
