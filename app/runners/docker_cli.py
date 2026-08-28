"""Small asynchronous Docker CLI adapter with bounded output capture."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    output_truncated: bool = False


class AttachedDockerProcess:
    """Interactive Docker CLI process with bounded candidate stderr capture."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        output_limit_bytes: int,
    ) -> None:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise RuntimeError("Docker CLI interactive pipes are unavailable")
        self._process = process
        self._stdin = process.stdin
        self._stdout = process.stdout
        self._capture = _BoundedCapture(output_limit_bytes)
        self._stderr_task = asyncio.create_task(self._capture.drain_stderr(process.stderr))

    async def write_line(self, payload: bytes) -> None:
        self._stdin.write(payload + b"\n")
        await self._stdin.drain()

    async def read_line(self, *, limit_bytes: int) -> bytes:
        line = await self._stdout.readline()
        if len(line) > limit_bytes or (len(line) == limit_bytes and not line.endswith(b"\n")):
            raise ValueError("Docker protocol response exceeded its limit")
        return line

    async def wait(self) -> int | None:
        await self._process.wait()
        await self._stderr_task
        return self._process.returncode

    async def terminate(self) -> None:
        if self._process.returncode is None:
            self._process.kill()
        await self._process.wait()
        await self._stderr_task

    @property
    def stderr(self) -> str:
        return self._capture.stderr

    @property
    def output_truncated(self) -> bool:
        return self._capture.truncated


class _BoundedCapture:
    def __init__(self, limit_bytes: int) -> None:
        self._limit_bytes = limit_bytes
        self._captured_bytes = 0
        self._stdout = bytearray()
        self._stderr = bytearray()
        self.truncated = False

    def append(self, destination: bytearray, chunk: bytes) -> None:
        remaining = self._limit_bytes - self._captured_bytes
        if remaining > 0:
            retained = chunk[:remaining]
            destination.extend(retained)
            self._captured_bytes += len(retained)
        if len(chunk) > remaining:
            self.truncated = True

    @property
    def stdout(self) -> str:
        return self._stdout.decode("utf-8", errors="replace")

    @property
    def stderr(self) -> str:
        return self._stderr.decode("utf-8", errors="replace")

    async def drain_stdout(self, reader: asyncio.StreamReader) -> None:
        await self._drain(reader, self._stdout)

    async def drain_stderr(self, reader: asyncio.StreamReader) -> None:
        await self._drain(reader, self._stderr)

    async def _drain(self, reader: asyncio.StreamReader, destination: bytearray) -> None:
        while chunk := await reader.read(64 * 1024):
            self.append(destination, chunk)


class DockerCli:
    """Invoke Docker without a shell and never retain unbounded command output."""

    def __init__(self, binary: str = "docker") -> None:
        self.binary = binary

    async def open(
        self,
        arguments: Sequence[str],
        *,
        output_limit_bytes: int,
    ) -> AttachedDockerProcess:
        process = await asyncio.create_subprocess_exec(
            self.binary,
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return AttachedDockerProcess(process, output_limit_bytes=output_limit_bytes)

    async def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
    ) -> CommandResult:
        started_at = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                self.binary,
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            return CommandResult(
                exit_code=None,
                stdout="",
                stderr="Docker CLI could not be started.",
                duration_seconds=time.monotonic() - started_at,
            )

        if process.stdout is None or process.stderr is None:
            process.kill()
            await process.wait()
            return CommandResult(
                exit_code=process.returncode,
                stdout="",
                stderr="Docker CLI pipes could not be created.",
                duration_seconds=time.monotonic() - started_at,
            )

        capture = _BoundedCapture(output_limit_bytes)
        stdout_task = asyncio.create_task(capture.drain_stdout(process.stdout))
        stderr_task = asyncio.create_task(capture.drain_stderr(process.stderr))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except TimeoutError:
            timed_out = True
            process.kill()
            await process.wait()
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        finally:
            await asyncio.gather(stdout_task, stderr_task)

        return CommandResult(
            exit_code=process.returncode,
            stdout=capture.stdout,
            stderr=capture.stderr,
            duration_seconds=time.monotonic() - started_at,
            timed_out=timed_out,
            output_truncated=capture.truncated,
        )
