from __future__ import annotations

from pathlib import Path

import pytest

from app.evaluator.engine import EvaluationEngine
from app.evaluator.models import EvaluationRequest, EvaluationStatus, Task
from app.runners.docker_cli import DockerCli
from app.runners.docker_runner import DockerPythonRunner, DockerSandboxConfig
from app.tasks.registry import RegisteredTask, TaskRegistry

pytestmark = pytest.mark.sandbox

IMAGE = "codejudge-python-sandbox:phase2"


def _runner(
    *,
    memory_mb: int = 256,
    pids_limit: int = 64,
    timeout_seconds: float = 5,
    output_limit_bytes: int = 1024,
) -> DockerPythonRunner:
    return DockerPythonRunner(
        DockerSandboxConfig(
            image=IMAGE,
            memory_mb=memory_mb,
            cpus=0.5,
            pids_limit=pids_limit,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=output_limit_bytes,
        )
    )


async def _require_sandbox(runner: DockerPythonRunner) -> None:
    capability = await runner.check_capability()
    if not capability.available:
        pytest.skip(capability.detail)


def _probe_task(
    tmp_path: Path,
    test_source: str,
    *,
    timeout_seconds: float = 5,
) -> RegisteredTask:
    tests_path = tmp_path / "tests"
    tests_path.mkdir()
    (tests_path / "test_probe.py").write_text(test_source, encoding="utf-8")
    return RegisteredTask(
        specification=Task(
            id="sandbox-probe",
            title="Sandbox probe",
            description="Verify one sandbox restriction.",
            language="python",
            timeout_seconds=timeout_seconds,
        ),
        tests_path=tests_path,
    )


async def test_correct_lru_submission(correct_lru: str) -> None:
    runner = _runner()
    await _require_sandbox(runner)
    engine = EvaluationEngine(TaskRegistry.default(), {"python": runner}, max_code_size=100_000)

    result = await engine.evaluate(
        EvaluationRequest(task_id="lru-cache", language="python", code=correct_lru)
    )

    assert result.status is EvaluationStatus.COMPLETED
    assert result.score == 100
    assert result.tests.passed == 8


async def test_incorrect_lru_submission(incorrect_lru: str) -> None:
    runner = _runner()
    await _require_sandbox(runner)
    engine = EvaluationEngine(TaskRegistry.default(), {"python": runner}, max_code_size=100_000)

    result = await engine.evaluate(
        EvaluationRequest(task_id="lru-cache", language="python", code=incorrect_lru)
    )

    assert result.status is EvaluationStatus.COMPLETED
    assert 0 < result.score < 100
    assert result.tests.failed > 0


async def test_syntax_error_is_structured() -> None:
    runner = _runner()
    await _require_sandbox(runner)
    engine = EvaluationEngine(TaskRegistry.default(), {"python": runner}, max_code_size=100_000)

    result = await engine.evaluate(
        EvaluationRequest(
            task_id="lru-cache",
            language="python",
            code="class LRUCache(\n",
        )
    )

    assert result.status is EvaluationStatus.COMPLETED
    assert result.score == 0
    assert result.findings[0].message == "Candidate code contains a syntax error."


async def test_infinite_loop_is_terminated_and_container_is_removed(tmp_path: Path) -> None:
    runner = _runner(timeout_seconds=2)
    await _require_sandbox(runner)
    task = _probe_task(
        tmp_path,
        "from solution import value\n\ndef test_value(): assert value == 1\n",
        timeout_seconds=2,
    )

    result = await runner.evaluate(task, "while True:\n    pass\n")

    assert result.timed_out is True
    remaining = await DockerCli().run(
        [
            "ps",
            "-a",
            "--filter",
            "label=codejudge.component=sandbox",
            "--format",
            "{{.Names}}",
        ],
        timeout_seconds=5,
        output_limit_bytes=4096,
    )
    assert remaining.exit_code == 0
    assert "codejudge-eval-" not in remaining.stdout


async def test_network_namespace_has_no_external_route(tmp_path: Path) -> None:
    runner = _runner()
    await _require_sandbox(runner)
    task = _probe_task(
        tmp_path,
        "from solution import network_is_blocked\n\n"
        "def test_network(): assert network_is_blocked()\n",
    )
    code = """
import errno
import socket


def network_is_blocked():
    connection = socket.socket()
    connection.settimeout(0.25)
    try:
        result = connection.connect_ex(("192.0.2.1", 80))
        return result in {errno.ENETUNREACH, errno.EHOSTUNREACH}
    finally:
        connection.close()
""".lstrip()

    result = await runner.evaluate(task, code)

    assert result.passed == 1


async def test_root_filesystem_is_read_only(tmp_path: Path) -> None:
    runner = _runner()
    await _require_sandbox(runner)
    task = _probe_task(
        tmp_path,
        "from solution import root_is_read_only\n\n"
        "def test_filesystem(): assert root_is_read_only()\n",
    )
    code = """
from pathlib import Path


def root_is_read_only():
    try:
        Path("/codejudge-probe").write_text("not allowed")
    except OSError:
        return True
    return False
""".lstrip()

    result = await runner.evaluate(task, code)

    assert result.passed == 1


async def test_candidate_runs_non_root_without_docker_socket(tmp_path: Path) -> None:
    runner = _runner()
    await _require_sandbox(runner)
    task = _probe_task(
        tmp_path,
        "from solution import restrictions_hold\n\n"
        "def test_identity(): assert restrictions_hold()\n",
    )
    code = """
import os
from pathlib import Path


def restrictions_hold():
    return os.geteuid() == 10001 and not Path("/var/run/docker.sock").exists()
""".lstrip()

    result = await runner.evaluate(task, code)

    assert result.passed == 1


async def test_host_environment_secret_is_not_forwarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEJUDGE_HOST_SECRET", "must-not-cross-boundary")
    runner = _runner()
    await _require_sandbox(runner)
    task = _probe_task(
        tmp_path,
        "from solution import host_secret_absent\n\n"
        "def test_environment(): assert host_secret_absent()\n",
    )
    code = """
import os


def host_secret_absent():
    return "CODEJUDGE_HOST_SECRET" not in os.environ
""".lstrip()

    result = await runner.evaluate(task, code)

    assert result.passed == 1


async def test_pid_limit_blocks_bounded_process_burst(tmp_path: Path) -> None:
    runner = _runner(pids_limit=32, timeout_seconds=8)
    await _require_sandbox(runner)
    task = _probe_task(
        tmp_path,
        "from solution import process_limit_enforced\n\n"
        "def test_processes(): assert process_limit_enforced()\n",
        timeout_seconds=8,
    )
    code = """
import subprocess
import sys


def process_limit_enforced():
    children = []
    limited = False
    try:
        for _ in range(40):
            try:
                children.append(
                    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
                )
            except OSError:
                limited = True
                break
    finally:
        for child in children:
            child.terminate()
        for child in children:
            child.wait()
    return limited
""".lstrip()

    result = await runner.evaluate(task, code)

    assert result.passed == 1


async def test_output_capture_is_bounded(tmp_path: Path) -> None:
    runner = _runner(output_limit_bytes=1024)
    await _require_sandbox(runner)
    task = _probe_task(
        tmp_path,
        "from solution import value\n\ndef test_value(): assert value == 1\n",
    )
    code = "print('x' * 8192)\nvalue = 1\n"

    result = await runner.evaluate(task, code)

    assert result.passed == 1
    assert result.output_truncated is True
    assert len(result.stdout.encode()) + len(result.stderr.encode()) <= 1024


async def test_memory_exhaustion_is_reported_from_container_metadata(tmp_path: Path) -> None:
    runner = _runner(memory_mb=128, timeout_seconds=8)
    await _require_sandbox(runner)
    task = _probe_task(
        tmp_path,
        "from solution import value\n\ndef test_value(): assert value == 1\n",
        timeout_seconds=8,
    )
    code = "chunks = []\nwhile True:\n    chunks.append(bytearray(8 * 1024 * 1024))\n"

    result = await runner.evaluate(task, code)

    assert result.oom_killed is True
    assert result.exit_code == 137
