"""Bounded, timeout-enforced subprocess execution for trusted analyzer binaries."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AnalyzerCommandResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    output_truncated: bool = False


class _BoundedCapture:
    def __init__(self, limit_bytes: int) -> None:
        self._limit_bytes = limit_bytes
        self._captured_bytes = 0
        self._stdout = bytearray()
        self._stderr = bytearray()
        self.truncated = False

    async def drain(self, reader: asyncio.StreamReader, destination: bytearray) -> None:
        while chunk := await reader.read(64 * 1024):
            remaining = self._limit_bytes - self._captured_bytes
            if remaining > 0:
                retained = chunk[:remaining]
                destination.extend(retained)
                self._captured_bytes += len(retained)
            if len(chunk) > remaining:
                self.truncated = True

    async def drain_stdout(self, reader: asyncio.StreamReader) -> None:
        await self.drain(reader, self._stdout)

    async def drain_stderr(self, reader: asyncio.StreamReader) -> None:
        await self.drain(reader, self._stderr)

    @property
    def stdout(self) -> str:
        return self._stdout.decode("utf-8", errors="replace")

    @property
    def stderr(self) -> str:
        return self._stderr.decode("utf-8", errors="replace")


class AnalyzerCommandRunner:
    """Run an analyzer without a shell or inherited host secrets."""

    def __init__(self, timeout_seconds: float, output_limit_bytes: int) -> None:
        if timeout_seconds <= 0 or output_limit_bytes <= 0:
            raise ValueError("Analyzer limits must be greater than zero")
        self._timeout_seconds = timeout_seconds
        self._output_limit_bytes = output_limit_bytes

    async def run(
        self, arguments: Sequence[str], *, working_directory: Path
    ) -> AnalyzerCommandResult:
        started_at = time.monotonic()
        environment = {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": os.defpath,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
        }
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                cwd=working_directory,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError:
            return AnalyzerCommandResult(
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=time.monotonic() - started_at,
            )

        if process.stdout is None or process.stderr is None:
            await _terminate(process)
            return AnalyzerCommandResult(
                exit_code=process.returncode,
                stdout="",
                stderr="",
                duration_seconds=time.monotonic() - started_at,
            )

        capture = _BoundedCapture(self._output_limit_bytes)
        stdout_task = asyncio.create_task(capture.drain_stdout(process.stdout))
        stderr_task = asyncio.create_task(capture.drain_stderr(process.stderr))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=self._timeout_seconds)
        except TimeoutError:
            timed_out = True
            await _terminate(process)
        except asyncio.CancelledError:
            await _terminate(process)
            raise
        finally:
            await asyncio.gather(stdout_task, stderr_task)

        return AnalyzerCommandResult(
            exit_code=process.returncode,
            stdout=capture.stdout,
            stderr=capture.stderr,
            duration_seconds=time.monotonic() - started_at,
            timed_out=timed_out,
            output_truncated=capture.truncated,
        )


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        process.kill()
    await process.wait()
