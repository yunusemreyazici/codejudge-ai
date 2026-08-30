# Sandbox resource limits

[← Project README](../../README.md) · [Documentation index](../README.md)

Resource controls bound candidate impact and make execution outcomes explicit. They do not replace
the threat model in [Security model](security-model.md).

## Default Docker limits

| Variable | Default | Enforcement |
| --- | ---: | --- |
| `SANDBOX_MEMORY_MB` | `256` | Docker memory limit |
| `SANDBOX_CPUS` | `0.5` | Fractional CPU allocation |
| `SANDBOX_PIDS_LIMIT` | `64` | Container process ceiling |
| `SANDBOX_TIMEOUT_SECONDS` | `5.0` | Global container wall-time ceiling |
| `SANDBOX_OUTPUT_LIMIT_BYTES` | `1048576` | Combined retained output ceiling |
| `SANDBOX_IMAGE` | `codejudge-python-sandbox:phase2` | Prebuilt execution image |

The effective execution timeout is the smaller of the task timeout and global sandbox timeout.
The root filesystem and candidate workspace are read-only; only bounded temporary storage is
writable.

## Memory and swap

On Docker/Linux, CodeJudge sets the memory limit and sets `memory_swap` to the same value. This
prevents the container from extending its configured memory budget through swap. Increasing memory
to mask an OOM test failure would weaken the intended boundary and is not an acceptable fix.

After process termination, CodeJudge inspects container state before removal. If this was not an
explicit timeout and immediate `State.OOMKilled` metadata is false, it performs a short bounded
retry for Docker `oom` events filtered by the exact container ID. OOM is true only when one of
those trusted signals exists.

Exit code 137 means SIGKILL and is not authoritative: timeout, an external kill, and OOM may all
produce it. An ambiguous 137 remains `oom_killed=false`.

## Timeouts

The timeout path is owned by CodeJudge:

1. wait for completion up to the effective limit;
2. mark `timed_out=true` when that wait expires;
3. terminate the exact evaluation container;
4. inspect terminal state before cleanup;
5. keep `oom_killed=false` for this explicit path.

This prevents timeout-related SIGKILL from being mislabeled as memory exhaustion.

## Output and logs

Candidate protocol output is bounded and decoded by trusted code. Combined retained stdout and
stderr cannot grow without limit. Docker log collection is also bounded. A missing or invalid
structured test report becomes a sanitized sandbox error rather than allowing candidate text to
control the result.

## Processes and cleanup

The PID ceiling constrains fork bombs and child-process growth. Every container has a scoped name
and component label. CodeJudge collects terminal metadata and exact-container events before
mandatory removal. CI asserts that no labeled sandbox container remains after sandbox and worker
end-to-end tests.

## Capability preflight

Docker capability checks have a bounded command timeout. Transient daemon responses, probe
timeouts, and malformed empty responses use limited retries with short deterministic delays. A
definitively missing sandbox image fails immediately. Failure responses expose a safe reason code,
not host Docker configuration.

Build and inspect the configured image:

```bash
docker build -t codejudge-python-sandbox:phase2 sandbox/
docker image inspect codejudge-python-sandbox:phase2
```

Run the authoritative sandbox suite with Docker required:

```bash
CODEJUDGE_REQUIRE_DOCKER=1 uv run pytest -v -m sandbox tests/sandbox
```
