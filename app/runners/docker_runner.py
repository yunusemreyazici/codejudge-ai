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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.evaluator.models import RunnerCapability, RunnerResult
from app.runners.docker_cli import CommandResult, DockerCli
from app.tasks.registry import RegisteredTask

logger = logging.getLogger(__name__)

_CONTROL_TIMEOUT_SECONDS = 5.0
_CONTROL_OUTPUT_LIMIT_BYTES = 64 * 1024
_REPORT_LIMIT_BYTES = 64 * 1024
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


class DockerPythonRunner:
    """Evaluate Python code in an intentionally restricted, disposable container."""

    def __init__(
        self,
        config: DockerSandboxConfig,
        *,
        cli: DockerClient | None = None,
        docker_binary: str = "docker",
    ) -> None:
        self._config = config
        self._cli = cli or DockerCli(docker_binary)
        self._docker_binary = docker_binary
        self._check_binary_path = cli is None

    async def check_capability(self) -> RunnerCapability:
        if self._check_binary_path and shutil.which(self._docker_binary) is None:
            return RunnerCapability(
                backend="docker",
                available=False,
                detail="Docker CLI is unavailable.",
            )

        daemon = await self._control_command(["info", "--format", "{{.ServerVersion}}"])
        if daemon.exit_code != 0 or daemon.timed_out:
            return RunnerCapability(
                backend="docker",
                available=False,
                detail="Docker daemon is unavailable.",
            )

        image = await self._control_command(
            ["image", "inspect", "--format", "{{.Id}}", self._config.image]
        )
        if image.exit_code != 0 or image.timed_out:
            return RunnerCapability(
                backend="docker",
                available=False,
                detail=f"Sandbox image '{self._config.image}' is unavailable.",
            )

        return RunnerCapability(
            backend="docker",
            available=True,
            detail=f"Docker sandbox image '{self._config.image}' is available.",
        )

    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
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
                if state.oom_killed:
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
            "--security-opt",
            "no-new-privileges=true",
            "--user",
            f"{_SANDBOX_UID}:{_SANDBOX_GID}",
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
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--mount",
            f"type=bind,source={workspace},target=/workspace,readonly",
            "--mount",
            f"type=bind,source={report_path},target=/results/report.json",
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
