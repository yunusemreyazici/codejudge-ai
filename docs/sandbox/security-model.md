# Sandbox security model

[← Project README](../../README.md) · [Documentation index](../README.md)

CodeJudge treats candidate source, coding-provider output, AI text, and generated adversarial tests
as untrusted. The default Docker runner materially reduces exposure, but it is a shared-kernel
container boundary—not a proof against Docker, runtime, or kernel escape.

## Threat model

Candidate code may attempt to:

- read hidden tests, references, host files, environment variables, or service credentials;
- contact external or local network services;
- write persistent files or modify evaluator code;
- fork excessively, consume CPU or memory, fill logs, or evade timeouts;
- inspect the Docker daemon or affect another evaluation;
- forge a structured test report.

The Docker daemon/runtime, host kernel, CodeJudge supervisor, packaged task material, PostgreSQL,
and deployment operator are trusted. A host or daemon compromise is outside this boundary.

## Implemented controls

Each evaluation gets a uniquely named disposable container with:

- non-root candidate UID/GID `10001`;
- `network=none`;
- a read-only root filesystem and read-only candidate workspace;
- a bounded writable `/tmp`;
- explicit CPU, memory plus swap, PID, wall-time, log, and output ceilings;
- all candidate capabilities dropped and `no-new-privileges` enabled;
- an explicit minimal environment rather than inherited host variables;
- no Docker socket, host bind mounts, hidden tests, references, or credentials;
- mandatory cleanup after terminal metadata is collected.

The supervisor retains only the limited identity-switch capability needed to launch the candidate
as non-root. Candidate code does not inherit that capability.

## Hidden-test boundary

Official tests run as trusted host-side assertions over a bounded JSON-lines protocol. The
candidate process receives one public-API operation at a time. It cannot inspect future operations,
expected values, assertions, reference source, or evaluator paths. The workspace exposes only
`solution.py`.

Real-Docker canary tests exercise hidden-test privacy, host-secret isolation, network denial,
read-only filesystems, user identity, Docker socket absence, PID limits, output bounds, OOM
classification, and cleanup.

## Timeout and OOM trust

An explicit CodeJudge timeout is recorded as `timed_out=true` and `oom_killed=false`. OOM is
recorded only from trusted Docker evidence: inspected container state or a bounded Docker `oom`
event whose actor exactly matches the evaluation container. Exit code 137 alone is ambiguous and
never establishes OOM.

Terminal state and delayed exact-container events are checked before removal so cleanup cannot
destroy authoritative evidence. See [Resource limits](resource-limits.md).

## Provider and AI boundaries

Coding providers receive versioned public task prompts, not hidden evaluator material. Generated
source is validated for nonblank content, persisted as an immutable artifact, and passed through
the ordinary sandbox.

AI judges receive candidate source under an explicitly untrusted data field and have no tools.
Responses must pass strict local schemas. Generated adversarial tests undergo structural policy
checks and must pass the trusted reference inside the sandbox before candidate comparison. These
tests remain supplemental and never change official correctness.

Provider endpoints and credentials are environment-only. Sanitized public failure categories and
allowlisted bounded detail tokens prevent raw response bodies or secrets from entering persisted
failure codes.

## Operational requirements

- Never mount the Docker socket into the API, workers, or candidate containers.
- Run CodeJudge on hosts where daemon access is tightly controlled.
- Keep the sandbox image pinned and verify its identity before accepting queued work.
- Keep `EXECUTION_BACKEND=docker` for untrusted code; Docker failure must not trigger local fallback.
- Keep databases and Redis off public interfaces or protect them with deployment-level controls.
- Treat generated reports and candidate archives as sensitive user-controlled content.
- Monitor disk, container cleanup, queue health, lease recovery, and repeated infrastructure errors.

## Residual risks

Containers share the host kernel. Kernel or runtime vulnerabilities, daemon compromise,
microarchitectural leakage, denial of service outside configured cgroup limits, and vulnerabilities
in trusted analyzers or task code remain possible. Highly hostile multi-tenant deployments should
consider an additional isolation layer such as gVisor, Kata Containers, or microVMs.

The `local` backend executes with the API user's permissions and provides no security boundary. It
is development-only.
