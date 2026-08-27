# CodeJudge AI Security Model

## Scope and threat model

Phase 4 assumes submitted Python is actively malicious. A candidate may loop forever, allocate
memory, emit unlimited output, spawn processes, inspect its environment, write files, access task
tests, attempt network connections, or probe host resources.

The Docker backend materially reduces exposure from these actions by running each evaluation in a
new, restricted container. Docker is not a perfect security boundary. The Docker daemon, container
runtime, and host kernel remain trusted. Kernel or runtime escapes, denial of service against the
Docker daemon, side channels, and vulnerabilities in Python or pytest are outside Phase 2's
protection.

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

Every Docker evaluation receives a unique name and labels, then runs with:

- UID and GID `10001`, reinforced by both the image and runtime arguments
- `--network none`
- hard memory and memory-swap ceilings
- a fractional CPU allocation
- a PID limit
- a read-only root filesystem
- all Linux capabilities dropped
- `no-new-privileges`
- a bounded writable `/tmp` tmpfs with `noexec`, `nosuid`, and `nodev`
- a read-only bind mount containing only `solution.py` and the required task tests
- one writable, pre-created report file with a 64 KiB file-size limit
- an application-level execution timeout
- bounded combined stdout/stderr capture that continues draining discarded bytes
- a bounded per-container Docker log using the local log driver

The runner never uses privileged mode, host networking, the host root filesystem, home or SSH
directories, or `/var/run/docker.sock`. Docker commands are argument arrays and never shell-built.
The report mount cannot create sibling files; the only general-purpose writable filesystem is the
size-bounded `/tmp` tmpfs. Containers are force-removed in a `finally` block after success,
failure, malformed reports, OOM, timeout, and cancellation.

## Secrets and environment

The host application's environment is not forwarded. The runner passes only:

- `PYTHONDONTWRITEBYTECODE=1`
- `PYTHONUNBUFFERED=1`
- the fixed candidate import path (`PYTHONPATH=/workspace`)
- the fixed in-container structured-report path

The image itself may define ordinary Python runtime variables. API keys, cloud credentials,
database URLs, CI tokens, GitHub tokens, and SSH variables from the API process are not copied into
the container. Do not put secrets in the sandbox image or candidate mounts.

## Docker daemon trust boundary

The CodeJudge API must be permitted to call the host Docker daemon, which is a highly privileged
service. Candidate containers never receive the Docker socket or Docker credentials. Operators
must protect daemon access, keep Docker and the host kernel patched, restrict who can change the
sandbox image, and monitor containers carrying the `codejudge.component=sandbox` label.

## Task-test visibility and result integrity

Task test source is never returned by the HTTP API. The required tests are nevertheless mounted
read-only inside the evaluation container, so malicious candidate code can inspect them. Phase 2
does not guarantee filesystem-level concealment of bundled tests or defend the pytest process from
all in-container test-tampering techniques. Strong anti-cheating and hidden-test confidentiality
need a different execution protocol in a later phase.

## Persisted source and database boundary

Phase 4 stores exact submitted source in PostgreSQL so a historical evaluation remains
explainable. Full source is omitted from list responses but deliberately returned by the UUID
detail endpoint. Authentication and tenant authorization are outside Phase 4, so operators must
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

## Remaining risks

- Container and kernel escape vulnerabilities
- Host resource pressure below or outside the configured cgroup controls, including Docker daemon
  overhead and temporary container logs
- Side channels between workloads sharing a kernel
- Malicious behavior against Python, pytest, or the trusted entrypoint
- Host I/O pressure up to the bounded container log and timeout ceilings
- Local backend execution, which has no security boundary and must not receive untrusted code
- Exposure of stored candidate source when the unauthenticated Phase 4 API is deployed without an
  external authorization layer
- Loss or compromise of PostgreSQL data or credentials; Phase 4 does not add encryption at rest or
  tenant isolation

Report security issues privately to the repository maintainers rather than opening a public issue
with exploit details.
