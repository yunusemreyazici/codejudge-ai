"""Local Python/pytest runner.

This runner provides process separation and temporary-workspace cleanup, but it is
not a security sandbox. Candidate code must be treated as having the permissions of
the API process until a real sandbox backend is introduced.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

from app.evaluator.models import RunnerCapability, RunnerResult
from app.tasks.registry import RegisteredTask

_REPORT_PLUGIN = """
import json
import os
import time


_started_at = time.monotonic()


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    stats = reporter.stats if reporter is not None else {}
    passed = len(stats.get("passed", []))
    reported_failed = len(stats.get("failed", [])) + len(stats.get("error", []))
    collected = int(getattr(session, "testscollected", 0))
    total = max(collected, passed + reported_failed)
    payload = {
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "duration_seconds": time.monotonic() - _started_at,
    }
    with open(os.environ["CODEJUDGE_REPORT_PATH"], "w", encoding="utf-8") as report:
        json.dump(payload, report)
""".lstrip()


class PythonRunner:
    """Run pytest in a child process within a disposable directory."""

    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
        started_at = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="codejudge-") as temporary_directory:
            workspace = Path(temporary_directory)
            report_path = workspace / "report.json"
            (workspace / "solution.py").write_text(code, encoding="utf-8")
            (workspace / "codejudge_reporter.py").write_text(_REPORT_PLUGIN, encoding="utf-8")
            shutil.copytree(task.tests_path, workspace / "task_tests")

            environment = os.environ.copy()
            environment.update(
                {
                    "CODEJUDGE_REPORT_PATH": str(report_path),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": str(workspace),
                }
            )

            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "task_tests",
                    "--tb=short",
                    "--disable-warnings",
                    "-p",
                    "codejudge_reporter",
                    cwd=workspace,
                    env=environment,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
            except OSError:
                return self._infrastructure_failure(started_at)

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=task.specification.timeout_seconds
                )
            except TimeoutError:
                await self._terminate(process)
                return RunnerResult(
                    exit_code=process.returncode,
                    stdout="",
                    stderr="",
                    duration_seconds=time.monotonic() - started_at,
                    passed=0,
                    failed=0,
                    total=0,
                    timed_out=True,
                    enforced_timeout_seconds=task.specification.timeout_seconds,
                )
            except asyncio.CancelledError:
                await self._terminate(process)
                raise

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            duration = time.monotonic() - started_at
            counts = self._read_report(report_path)
            if counts is None:
                return RunnerResult(
                    exit_code=process.returncode,
                    stdout=stdout,
                    stderr=stderr,
                    duration_seconds=duration,
                    passed=0,
                    failed=0,
                    total=0,
                    infrastructure_error="Pytest did not produce a structured report.",
                )

            passed, failed, total = counts
            return RunnerResult(
                exit_code=process.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
                passed=passed,
                failed=failed,
                total=total,
                enforced_timeout_seconds=task.specification.timeout_seconds,
            )

    async def check_capability(self) -> RunnerCapability:
        return RunnerCapability(
            backend="local",
            available=True,
            detail="Local execution is available but is not isolated.",
        )

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            process.kill()
        await process.communicate()

    @staticmethod
    def _read_report(report_path: Path) -> tuple[int, int, int] | None:
        try:
            raw: object = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        passed = raw.get("passed")
        failed = raw.get("failed")
        total = raw.get("total")
        if not isinstance(passed, int) or isinstance(passed, bool):
            return None
        if not isinstance(failed, int) or isinstance(failed, bool):
            return None
        if not isinstance(total, int) or isinstance(total, bool):
            return None
        if passed < 0 or failed < 0 or total != passed + failed:
            return None
        return passed, failed, total

    @staticmethod
    def _infrastructure_failure(started_at: float) -> RunnerResult:
        return RunnerResult(
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=time.monotonic() - started_at,
            passed=0,
            failed=0,
            total=0,
            infrastructure_error="The Python test process could not be started.",
        )
