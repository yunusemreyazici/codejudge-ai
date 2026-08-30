"""Restricted Docker-backed Python evaluation runner."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import stat
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from app.evaluator.models import RunnerCapability, RunnerResult
from app.runners.docker_cli import CommandResult, DockerCli
from app.runners.trusted_harness import (
    HarnessProtocolError,
    HarnessReport,
    TrustedOfficialHarness,
)
from app.tasks.registry import RegisteredTask

logger = logging.getLogger(__name__)

_CONTROL_TIMEOUT_SECONDS = 10.0
_CONTROL_OUTPUT_LIMIT_BYTES = 64 * 1024
_REPORT_LIMIT_BYTES = 64 * 1024
_CAPABILITY_ATTEMPTS = 3
_CAPABILITY_RETRY_DELAY_SECONDS = 0.25
_OOM_EVENT_ATTEMPTS = 9
_OOM_EVENT_RETRY_DELAY_SECONDS = 0.25
_SANDBOX_UID = 10001
_SANDBOX_GID = 10001


class DockerClient(Protocol):
    async def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
    ) -> CommandResult: ...

    async def open(
        self,
        arguments: Sequence[str],
        *,
        output_limit_bytes: int,
    ) -> InteractiveDockerProcess: ...


class InteractiveDockerProcess(Protocol):
    async def write_line(self, payload: bytes) -> None: ...

    async def read_line(self, *, limit_bytes: int) -> bytes: ...

    async def wait(self) -> int | None: ...

    async def terminate(self) -> None: ...

    @property
    def stderr(self) -> str: ...

    @property
    def output_truncated(self) -> bool: ...


class OfficialHarness(Protocol):
    async def evaluate(
        self,
        task_id: str,
        task_revision: int,
        transport: _CandidateTransport,
    ) -> HarnessReport: ...


class DockerCapabilityFailure(StrEnum):
    CLI_MISSING = "docker_cli_missing"
    DAEMON_UNAVAILABLE = "docker_daemon_unavailable"
    PROBE_TIMEOUT = "docker_probe_timeout"
    IMAGE_MISSING = "sandbox_image_missing"
    PROBE_FAILED = "docker_probe_failed"


@dataclass(frozen=True, slots=True)
class DockerSandboxConfig:
    image: str
    memory_mb: int
    cpus: float
    pids_limit: int
    timeout_seconds: float
    output_limit_bytes: int

    def __post_init__(self) -> None:
        numeric_values = (
            self.memory_mb,
            self.cpus,
            self.pids_limit,
            self.timeout_seconds,
            self.output_limit_bytes,
        )
        if not self.image.strip():
            raise ValueError("Sandbox image must not be blank")
        if any(value <= 0 for value in numeric_values):
            raise ValueError("Sandbox resource limits must be greater than zero")


@dataclass(frozen=True, slots=True)
class _ContainerState:
    exit_code: int
    oom_killed: bool


@dataclass(frozen=True, slots=True)
class _TestReport:
    passed: int
    failed: int
    total: int
    syntax_error: bool
    import_error: bool


class _CandidateTransport:
    def __init__(self, process: InteractiveDockerProcess) -> None:
        self._process = process
        self._next_id = 1

    async def request(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        request_id = self._next_id
        self._next_id += 1
        encoded = json.dumps({"id": request_id, **payload}, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _REPORT_LIMIT_BYTES:
            raise HarnessProtocolError("Candidate protocol request exceeded its limit")
        await self._process.write_line(encoded)
        line = await self._process.read_line(limit_bytes=_REPORT_LIMIT_BYTES)
        try:
            response: object = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HarnessProtocolError("Candidate protocol returned invalid JSON") from error
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise HarnessProtocolError("Candidate protocol response ID mismatch")
        return response

    async def shutdown(self) -> None:
        response = await self.request({"op": "shutdown"})
        if response.get("ok") is not True:
            raise HarnessProtocolError("Candidate supervisor rejected shutdown")


class DockerPythonRunner:
    """Evaluate Python code in an intentionally restricted, disposable container."""

    def __init__(
        self,
        config: DockerSandboxConfig,
        *,
        cli: DockerClient | None = None,
        docker_binary: str = "docker",
        capability_attempts: int = _CAPABILITY_ATTEMPTS,
        capability_retry_delay_seconds: float = _CAPABILITY_RETRY_DELAY_SECONDS,
        harness: OfficialHarness | None = None,
    ) -> None:
        self._config = config
        self._cli = cli or DockerCli(docker_binary)
        self._docker_binary = docker_binary
        self._check_binary_path = cli is None
        self._capability_attempts = capability_attempts
        self._capability_retry_delay_seconds = capability_retry_delay_seconds
        self._harness = harness or TrustedOfficialHarness()
        if capability_attempts <= 0 or capability_retry_delay_seconds < 0:
            raise ValueError("Capability retry settings must be bounded and nonnegative")

    async def check_capability(self) -> RunnerCapability:
        if self._check_binary_path and shutil.which(self._docker_binary) is None:
            return RunnerCapability(
                backend="docker",
                available=False,
                detail="Docker CLI is unavailable.",
                reason=DockerCapabilityFailure.CLI_MISSING,
            )

        daemon = await self._capability_probe(
            ["info", "--format", "{{.ServerVersion}}"],
            retry_nonzero=True,
        )
        daemon_failure = self._probe_failure(daemon, require_output=True)
        if daemon_failure is not None:
            return RunnerCapability(
                backend="docker",
                available=False,
                detail=self._capability_detail(daemon_failure),
                reason=daemon_failure,
            )

        image = await self._capability_probe(
            ["image", "inspect", "--format", "{{.Id}}", self._config.image],
            retry_nonzero=False,
        )
        image_failure = self._probe_failure(image, require_output=True)
        if image_failure is not None:
            reason = image_failure
            if image.exit_code not in {None, 0} and not image.timed_out:
                daemon_confirmation = await self._capability_probe(
                    ["info", "--format", "{{.ServerVersion}}"],
                    retry_nonzero=True,
                )
                confirmation_failure = self._probe_failure(daemon_confirmation, require_output=True)
                reason = confirmation_failure or DockerCapabilityFailure.IMAGE_MISSING
            return RunnerCapability(
                backend="docker",
                available=False,
                detail=self._capability_detail(reason),
                reason=reason,
            )

        return RunnerCapability(
            backend="docker",
            available=True,
            detail=f"Docker sandbox image '{self._config.image}' is available.",
        )

    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
        """Run repository official tests without mounting private evaluator assets."""

        started_at = time.monotonic()
        capability = await self.check_capability()
        if not capability.available:
            return self._infrastructure_failure(started_at, capability.detail)

        evaluation_id = uuid.uuid4().hex
        container_name = f"codejudge-eval-{evaluation_id}"
        effective_timeout = min(task.specification.timeout_seconds, self._config.timeout_seconds)
        container_created = False
        create_attempted = False
        process: InteractiveDockerProcess | None = None
        event_since = self._event_timestamp()

        with tempfile.TemporaryDirectory(prefix="codejudge-docker-") as temporary_directory:
            workspace = Path(temporary_directory) / "workspace"
            self._prepare_candidate_workspace(workspace, code)
            try:
                create_attempted = True
                create_result = await self._control_command(
                    self._official_create_arguments(container_name, evaluation_id, workspace)
                )
                if create_result.exit_code != 0 or create_result.timed_out:
                    return self._infrastructure_failure(
                        started_at, "Docker could not create the evaluation container."
                    )
                container_created = True
                create_output = create_result.stdout.strip().splitlines()
                container_id = create_output[0] if create_output else container_name

                try:
                    process = await self._cli.open(
                        ["start", "--attach", "--interactive", container_name],
                        output_limit_bytes=self._config.output_limit_bytes,
                    )
                except OSError:
                    return self._infrastructure_failure(
                        started_at, "Docker could not start the evaluation container."
                    )

                try:
                    async with asyncio.timeout(effective_timeout):
                        transport = _CandidateTransport(process)
                        report = await self._harness.evaluate(
                            task.specification.id,
                            task.revision,
                            transport,
                        )
                        await transport.shutdown()
                        exit_code = await process.wait()
                except TimeoutError:
                    await process.terminate()
                    await self._kill(container_name)
                    state = await self._inspect_state(container_name)
                    return RunnerResult(
                        exit_code=None if state is None else state.exit_code,
                        stdout="",
                        stderr=process.stderr,
                        duration_seconds=time.monotonic() - started_at,
                        passed=0,
                        failed=0,
                        total=0,
                        timed_out=True,
                        enforced_timeout_seconds=effective_timeout,
                        output_truncated=process.output_truncated,
                    )
                except HarnessProtocolError as error:
                    await process.terminate()
                    await self._kill(container_name)
                    state = await self._inspect_state(container_name)
                    oom_killed = state is not None and state.oom_killed
                    if not oom_killed and state is not None and state.exit_code == 137:
                        oom_killed = await self._has_oom_event(
                            container_id,
                            container_name,
                            event_since,
                            self._event_timestamp(),
                        )
                    if oom_killed:
                        return RunnerResult(
                            exit_code=None if state is None else state.exit_code,
                            stdout="",
                            stderr=process.stderr,
                            duration_seconds=time.monotonic() - started_at,
                            passed=0,
                            failed=0,
                            total=0,
                            enforced_timeout_seconds=effective_timeout,
                            output_truncated=process.output_truncated,
                            oom_killed=True,
                        )
                    return RunnerResult(
                        exit_code=None,
                        stdout="",
                        stderr=process.stderr,
                        duration_seconds=time.monotonic() - started_at,
                        passed=0,
                        failed=0,
                        total=0,
                        enforced_timeout_seconds=effective_timeout,
                        output_truncated=process.output_truncated,
                        sandbox_error=str(error),
                    )

                state = await self._inspect_state(container_name)
                if state is None:
                    return self._infrastructure_failure(
                        started_at, "Docker could not inspect the evaluation container."
                    )
                oom_killed = state.oom_killed
                if not oom_killed and state.exit_code == 137:
                    oom_killed = await self._has_oom_event(
                        container_id,
                        container_name,
                        event_since,
                        self._event_timestamp(),
                    )
                if oom_killed:
                    return RunnerResult(
                        exit_code=state.exit_code,
                        stdout="",
                        stderr=process.stderr,
                        duration_seconds=time.monotonic() - started_at,
                        passed=0,
                        failed=0,
                        total=0,
                        enforced_timeout_seconds=effective_timeout,
                        output_truncated=process.output_truncated,
                        oom_killed=True,
                    )
                if state.exit_code != 0:
                    return RunnerResult(
                        exit_code=state.exit_code,
                        stdout="",
                        stderr=process.stderr,
                        duration_seconds=time.monotonic() - started_at,
                        passed=0,
                        failed=0,
                        total=0,
                        enforced_timeout_seconds=effective_timeout,
                        output_truncated=process.output_truncated,
                        sandbox_error="Sandbox exited without a valid structured test report.",
                    )
                return RunnerResult(
                    exit_code=exit_code,
                    stdout="",
                    stderr=process.stderr,
                    duration_seconds=time.monotonic() - started_at,
                    passed=report.passed,
                    failed=report.failed,
                    total=report.total,
                    enforced_timeout_seconds=effective_timeout,
                    output_truncated=process.output_truncated,
                    syntax_error=report.syntax_error,
                    import_error=report.import_error,
                )
            except asyncio.CancelledError:
                if process is not None:
                    await asyncio.shield(process.terminate())
                if container_created:
                    await asyncio.shield(self._kill(container_name))
                raise
            finally:
                if create_attempted:
                    await asyncio.shield(
                        self._remove(container_name, log_failure=container_created)
                    )

    async def evaluate_generated_tests(self, task: RegisteredTask, code: str) -> RunnerResult:
        """Run already-validated Phase 6 generated pytest in a separate sandbox."""
        started_at = time.monotonic()
        capability = await self.check_capability()
        if not capability.available:
            return self._infrastructure_failure(started_at, capability.detail)

        evaluation_id = uuid.uuid4().hex
        container_name = f"codejudge-eval-{evaluation_id}"
        effective_timeout = min(
            task.specification.timeout_seconds,
            self._config.timeout_seconds,
        )
        container_created = False
        create_attempted = False
        container_id: str | None = None
        event_since = self._event_timestamp()

        with tempfile.TemporaryDirectory(prefix="codejudge-docker-") as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            results_directory = root / "results"
            report_path = results_directory / "report.json"
            self._prepare_workspace(workspace, report_path, task, code)

            try:
                create_attempted = True
                create_result = await self._control_command(
                    self._create_arguments(
                        container_name,
                        evaluation_id,
                        workspace,
                        report_path,
                    )
                )
                if create_result.exit_code != 0 or create_result.timed_out:
                    return self._infrastructure_failure(
                        started_at, "Docker could not create the evaluation container."
                    )
                container_created = True
                create_output = create_result.stdout.strip().splitlines()
                container_id = create_output[0] if create_output else None

                start_result = await self._cli.run(
                    ["start", "--attach", container_name],
                    timeout_seconds=effective_timeout,
                    output_limit_bytes=self._config.output_limit_bytes,
                )
                if start_result.exit_code is None:
                    return self._infrastructure_failure(
                        started_at, "Docker could not start the evaluation container."
                    )
                if start_result.timed_out:
                    await self._kill(container_name)
                    state = await self._inspect_state(container_name)
                    return RunnerResult(
                        exit_code=None if state is None else state.exit_code,
                        stdout=start_result.stdout,
                        stderr=start_result.stderr,
                        duration_seconds=time.monotonic() - started_at,
                        passed=0,
                        failed=0,
                        total=0,
                        timed_out=True,
                        enforced_timeout_seconds=effective_timeout,
                        output_truncated=start_result.output_truncated,
                    )

                state = await self._inspect_state(container_name)
                if state is None:
                    return self._infrastructure_failure(
                        started_at, "Docker could not inspect the evaluation container."
                    )
                oom_killed = state.oom_killed
                if not oom_killed and state.exit_code == 137:
                    oom_killed = await self._has_oom_event(
                        container_id or container_name,
                        container_name,
                        event_since,
                        self._event_timestamp(),
                    )
                if oom_killed:
                    return RunnerResult(
                        exit_code=state.exit_code,
                        stdout=start_result.stdout,
                        stderr=start_result.stderr,
                        duration_seconds=time.monotonic() - started_at,
                        passed=0,
                        failed=0,
                        total=0,
                        enforced_timeout_seconds=effective_timeout,
                        output_truncated=start_result.output_truncated,
                        oom_killed=True,
                    )

                report = self._read_report(report_path)
                if report is None:
                    return RunnerResult(
                        exit_code=state.exit_code,
                        stdout=start_result.stdout,
                        stderr=start_result.stderr,
                        duration_seconds=time.monotonic() - started_at,
                        passed=0,
                        failed=0,
                        total=0,
                        enforced_timeout_seconds=effective_timeout,
                        output_truncated=start_result.output_truncated,
                        sandbox_error="Sandbox exited without a valid structured test report.",
                    )

                return RunnerResult(
                    exit_code=state.exit_code,
                    stdout=start_result.stdout,
                    stderr=start_result.stderr,
                    duration_seconds=time.monotonic() - started_at,
                    passed=report.passed,
                    failed=report.failed,
                    total=report.total,
                    enforced_timeout_seconds=effective_timeout,
                    output_truncated=start_result.output_truncated,
                    syntax_error=report.syntax_error,
                    import_error=report.import_error,
                )
            except asyncio.CancelledError:
                if container_created:
                    await asyncio.shield(self._kill(container_name))
                raise
            finally:
                if create_attempted:
                    await asyncio.shield(
                        self._remove(container_name, log_failure=container_created)
                    )

    def _create_arguments(
        self,
        container_name: str,
        evaluation_id: str,
        workspace: Path,
        report_path: Path,
    ) -> list[str]:
        memory = f"{self._config.memory_mb}m"
        log_size_kib = max(1, (self._config.output_limit_bytes + 1023) // 1024)
        return [
            "create",
            "--name",
            container_name,
            "--label",
            "codejudge.component=sandbox",
            "--label",
            f"codejudge.evaluation_id={evaluation_id}",
            "--log-driver",
            "local",
            "--log-opt",
            f"max-size={log_size_kib}k",
            "--log-opt",
            "max-file=1",
            "--log-opt",
            "compress=false",
            "--network",
            "none",
            "--memory",
            memory,
            "--memory-swap",
            memory,
            "--cpus",
            f"{self._config.cpus:g}",
            "--pids-limit",
            str(self._config.pids_limit),
            "--ulimit",
            f"fsize={_REPORT_LIMIT_BYTES}:{_REPORT_LIMIT_BYTES}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--security-opt",
            "no-new-privileges=true",
            "--workdir",
            "/workspace",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONUNBUFFERED=1",
            "--env",
            "PYTHONPATH=/workspace",
            "--env",
            "CODEJUDGE_REPORT_PATH=/results/report.json",
            "--env",
            "CODEJUDGE_GENERATED_PYTEST=1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--mount",
            f"type=bind,source={workspace},target=/workspace,readonly",
            "--mount",
            f"type=bind,source={report_path},target=/results/report.json",
            "--init",
            self._config.image,
        ]

    def _official_create_arguments(
        self,
        container_name: str,
        evaluation_id: str,
        workspace: Path,
    ) -> list[str]:
        memory = f"{self._config.memory_mb}m"
        log_size_kib = max(1, (self._config.output_limit_bytes + 1023) // 1024)
        return [
            "create",
            "--interactive",
            "--name",
            container_name,
            "--label",
            "codejudge.component=sandbox",
            "--label",
            f"codejudge.evaluation_id={evaluation_id}",
            "--log-driver",
            "local",
            "--log-opt",
            f"max-size={log_size_kib}k",
            "--log-opt",
            "max-file=1",
            "--log-opt",
            "compress=false",
            "--network",
            "none",
            "--memory",
            memory,
            "--memory-swap",
            memory,
            "--cpus",
            f"{self._config.cpus:g}",
            "--pids-limit",
            str(self._config.pids_limit),
            "--ulimit",
            f"fsize={_REPORT_LIMIT_BYTES}:{_REPORT_LIMIT_BYTES}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--security-opt",
            "no-new-privileges=true",
            "--workdir",
            "/workspace",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONUNBUFFERED=1",
            "--env",
            "PYTHONPATH=/workspace",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--mount",
            f"type=bind,source={workspace},target=/workspace,readonly",
            "--init",
            self._config.image,
        ]

    @staticmethod
    def _prepare_workspace(
        workspace: Path,
        report_path: Path,
        task: RegisteredTask,
        code: str,
    ) -> None:
        workspace.mkdir()
        report_path.parent.mkdir()
        report_path.touch(mode=0o666)
        (workspace / "solution.py").write_text(code, encoding="utf-8")
        shutil.copytree(task.tests_path, workspace / "task_tests")
        for path in workspace.rglob("*"):
            path.chmod(0o555 if path.is_dir() else 0o444)
        workspace.chmod(0o555)
        report_path.chmod(0o666)

    @staticmethod
    def _prepare_candidate_workspace(workspace: Path, code: str) -> None:
        workspace.mkdir()
        solution = workspace / "solution.py"
        solution.write_text(code, encoding="utf-8")
        solution.chmod(0o444)
        workspace.chmod(0o555)

    async def _inspect_state(self, container_name: str) -> _ContainerState | None:
        result = await self._control_command(
            ["inspect", "--format", "{{json .State}}", container_name]
        )
        if result.exit_code != 0 or result.timed_out:
            return None
        try:
            raw: object = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        if not isinstance(raw, dict):
            return None
        exit_code = raw.get("ExitCode")
        oom_killed = raw.get("OOMKilled")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            return None
        if not isinstance(oom_killed, bool):
            return None
        return _ContainerState(exit_code=exit_code, oom_killed=oom_killed)

    async def _kill(self, container_name: str) -> None:
        await self._control_command(["kill", container_name])

    async def _remove(self, container_name: str, *, log_failure: bool = True) -> None:
        result = await self._control_command(["rm", "--force", container_name])
        if result.exit_code != 0 and log_failure:
            logger.error("sandbox cleanup failed container_name=%s", container_name)

    async def _control_command(self, arguments: Sequence[str]) -> CommandResult:
        return await self._cli.run(
            arguments,
            timeout_seconds=_CONTROL_TIMEOUT_SECONDS,
            output_limit_bytes=_CONTROL_OUTPUT_LIMIT_BYTES,
        )

    async def _capability_probe(
        self,
        arguments: Sequence[str],
        *,
        retry_nonzero: bool,
    ) -> CommandResult:
        result = await self._control_command(arguments)
        for attempt in range(1, self._capability_attempts):
            failure = self._probe_failure(result, require_output=True)
            if failure is None or (
                failure is DockerCapabilityFailure.DAEMON_UNAVAILABLE and not retry_nonzero
            ):
                break
            await asyncio.sleep(self._capability_retry_delay_seconds * attempt)
            result = await self._control_command(arguments)
        return result

    @staticmethod
    def _probe_failure(
        result: CommandResult, *, require_output: bool
    ) -> DockerCapabilityFailure | None:
        if result.timed_out:
            return DockerCapabilityFailure.PROBE_TIMEOUT
        if result.exit_code is None:
            return DockerCapabilityFailure.PROBE_FAILED
        if result.exit_code != 0:
            return DockerCapabilityFailure.DAEMON_UNAVAILABLE
        if require_output and not result.stdout.strip():
            return DockerCapabilityFailure.PROBE_FAILED
        return None

    def _capability_detail(self, reason: DockerCapabilityFailure) -> str:
        if reason is DockerCapabilityFailure.PROBE_TIMEOUT:
            return "Docker capability probe timed out."
        if reason is DockerCapabilityFailure.PROBE_FAILED:
            return "Docker capability probe failed."
        if reason is DockerCapabilityFailure.IMAGE_MISSING:
            return f"Sandbox image '{self._config.image}' is unavailable."
        return "Docker daemon is unavailable."

    async def _has_oom_event(
        self,
        container_reference: str,
        container_name: str,
        since: str,
        until: str,
    ) -> bool:
        event_until = until
        for attempt in range(_OOM_EVENT_ATTEMPTS):
            result = await self._control_command(
                [
                    "events",
                    "--since",
                    since,
                    "--until",
                    event_until,
                    "--filter",
                    f"container={container_reference}",
                    "--filter",
                    "event=oom",
                    "--format",
                    "{{json .}}",
                ]
            )
            if result.exit_code != 0 or result.timed_out or result.output_truncated:
                return False
            for line in result.stdout.splitlines():
                try:
                    event: object = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                action = event.get("Action", event.get("status"))
                actor = event.get("Actor")
                actor_id = actor.get("ID") if isinstance(actor, dict) else event.get("id")
                attributes = actor.get("Attributes") if isinstance(actor, dict) else None
                name = attributes.get("name") if isinstance(attributes, dict) else None
                exact_actor = actor_id == container_reference or name == container_name
                if action == "oom" and exact_actor:
                    return True
            if attempt + 1 < _OOM_EVENT_ATTEMPTS:
                await asyncio.sleep(_OOM_EVENT_RETRY_DELAY_SECONDS)
                event_until = self._event_timestamp()
        return False

    @staticmethod
    def _event_timestamp() -> str:
        return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

    @staticmethod
    def _read_report(report_path: Path) -> _TestReport | None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(report_path, flags)
        except OSError:
            return None
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _REPORT_LIMIT_BYTES:
                return None
            payload = os.read(descriptor, _REPORT_LIMIT_BYTES + 1)
        except OSError:
            return None
        finally:
            os.close(descriptor)
        if len(payload) > _REPORT_LIMIT_BYTES:
            return None
        try:
            raw: object = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        passed = raw.get("passed")
        failed = raw.get("failed")
        total = raw.get("total")
        syntax_error = raw.get("syntax_error", False)
        import_error = raw.get("import_error", False)
        if not isinstance(passed, int) or isinstance(passed, bool):
            return None
        if not isinstance(failed, int) or isinstance(failed, bool):
            return None
        if not isinstance(total, int) or isinstance(total, bool):
            return None
        if not isinstance(syntax_error, bool) or not isinstance(import_error, bool):
            return None
        if passed < 0 or failed < 0 or total != passed + failed:
            return None
        return _TestReport(
            passed=passed,
            failed=failed,
            total=total,
            syntax_error=syntax_error,
            import_error=import_error,
        )

    @staticmethod
    def _infrastructure_failure(started_at: float, message: str) -> RunnerResult:
        return RunnerResult(
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=time.monotonic() - started_at,
            passed=0,
            failed=0,
            total=0,
            infrastructure_error=message,
        )
