from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from app.benchmarks.datasets import BenchmarkDatasetRegistry
from app.core.config import Settings
from app.evaluator.models import Task
from app.runners.docker_runner import DockerPythonRunner, DockerSandboxConfig
from app.runners.trusted_harness import CandidateTransport, HarnessReport
from app.tasks.registry import RegisteredTask, TaskRegistry

pytestmark = pytest.mark.sandbox

_TEST_CANARY = "CODEJUDGE_PRIVATE_TEST_CANARY_7f927bda0ed84a3f"
_REFERENCE_CANARY = "CODEJUDGE_PRIVATE_REFERENCE_CANARY_caa1b39f8e6d4504"

_MALICIOUS_CANDIDATE = r"""
import os
from pathlib import Path


def privacy_probe():
    private_prefix = "CODEJUDGE_" + "PRIVATE_"
    matches = []
    visible = []
    sensitive = []
    roots = [Path("/workspace"), Path("/tmp"), Path("/app"), Path("/opt/codejudge")]
    for root in roots:
        try:
            candidates = [root, *root.rglob("*")]
        except OSError:
            continue
        for path in candidates[:5000]:
            name = str(path)
            visible.append(name)
            lowered = name.lower()
            private_names = (
                "task_tests",
                "reference/solution",
                "test_private_canary",
                "task.yaml",
            )
            if any(token in lowered for token in private_names):
                sensitive.append(name)
            try:
                if path.is_file() and path.stat().st_size <= 262_144:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                    if private_prefix in content:
                        matches.append(name)
            except OSError:
                pass

    proc_reads = []
    parent_mem_readable = False
    parent_stdin_readable = False
    try:
        descriptor = os.open(f"/proc/{os.getppid()}/mem", os.O_RDONLY)
    except OSError:
        pass
    else:
        parent_mem_readable = True
        os.close(descriptor)
    try:
        descriptor = os.open(
            f"/proc/{os.getppid()}/fd/0",
            os.O_RDONLY | os.O_NONBLOCK,
        )
    except OSError:
        pass
    else:
        parent_stdin_readable = True
        os.close(descriptor)
    try:
        process_paths = list(Path("/proc").iterdir())
    except OSError:
        process_paths = []
    for process in process_paths:
        if not process.name.isdigit():
            continue
        for leaf in ("cmdline", "environ"):
            target = process / leaf
            try:
                content = target.read_bytes()[:65_536].decode("utf-8", errors="ignore")
                proc_reads.append(str(target))
                if private_prefix in content:
                    matches.append(str(target))
            except OSError:
                pass
        descriptors = process / "fd"
        try:
            entries = list(descriptors.iterdir())
        except OSError:
            continue
        for descriptor in entries:
            try:
                target = os.readlink(descriptor)
                if private_prefix in target:
                    matches.append(str(descriptor))
            except OSError:
                pass

    return {
        "matches": sorted(set(matches)),
        "workspace": sorted(path.name for path in Path("/workspace").iterdir()),
        "sensitive": sorted(set(sensitive)),
        "euid": os.geteuid(),
        "egid": os.getegid(),
        "cap_eff": next(
            (
                line.split(":", 1)[1].strip()
                for line in Path("/proc/self/status").read_text().splitlines()
                if line.startswith("CapEff:")
            ),
            None,
        ),
        "proc_reads": proc_reads,
        "parent_mem_readable": parent_mem_readable,
        "parent_stdin_readable": parent_stdin_readable,
    }
""".lstrip()


class _PrivacyHarness:
    def __init__(self, expected_task_id: str, expected_revision: int) -> None:
        self._expected_task_id = expected_task_id
        self._expected_revision = expected_revision

    async def evaluate(
        self,
        task_id: str,
        task_revision: int,
        transport: CandidateTransport,
    ) -> HarnessReport:
        response = await transport.request(
            {
                "op": "run_case",
                "steps": [{"op": "call_function", "symbol": "privacy_probe", "args": []}],
            }
        )
        candidate = response.get("candidate")
        if not response.get("ok") or not isinstance(candidate, Mapping):
            return HarnessReport(passed=0, failed=1, total=1)
        outcomes = candidate.get("outcomes")
        if not isinstance(outcomes, list) or len(outcomes) != 1:
            return HarnessReport(passed=0, failed=1, total=1)
        outcome = outcomes[0]
        result = outcome.get("result") if isinstance(outcome, Mapping) else None
        safe = (
            task_id == self._expected_task_id
            and task_revision == self._expected_revision
            and isinstance(result, Mapping)
            and result.get("matches") == []
            and result.get("workspace") == ["solution.py"]
            and result.get("sensitive") == []
            and result.get("euid") == 10001
            and result.get("egid") == 10001
            and result.get("cap_eff") == "0000000000000000"
            and result.get("parent_mem_readable") is False
            and result.get("parent_stdin_readable") is False
        )
        return HarnessReport(passed=int(safe), failed=int(not safe), total=1)


def _runner(expected_task_id: str, expected_revision: int) -> DockerPythonRunner:
    settings = Settings()
    return DockerPythonRunner(
        DockerSandboxConfig(
            image=settings.sandbox_image,
            memory_mb=settings.sandbox_memory_mb,
            cpus=settings.sandbox_cpus,
            pids_limit=settings.sandbox_pids_limit,
            timeout_seconds=settings.sandbox_timeout_seconds,
            output_limit_bytes=settings.sandbox_output_limit_bytes,
        ),
        harness=_PrivacyHarness(expected_task_id, expected_revision),
    )


@pytest.mark.parametrize("repetition", range(3))
async def test_candidate_runtime_cannot_read_private_tests_or_reference(
    tmp_path: Path, repetition: int
) -> None:
    del repetition
    tests_path = tmp_path / "official_tests"
    reference_path = tmp_path / "reference" / "solution.py"
    tests_path.mkdir()
    reference_path.parent.mkdir()
    (tests_path / "test_private_canary.py").write_text(
        f'PRIVATE_TEST = "{_TEST_CANARY}"\n', encoding="utf-8"
    )
    reference_path.write_text(f'PRIVATE_REFERENCE = "{_REFERENCE_CANARY}"\n', encoding="utf-8")
    task = RegisteredTask(
        specification=Task(
            id="privacy-probe",
            title="Runtime privacy probe",
            description="Internal runtime privacy verification.",
            language="python",
            timeout_seconds=5,
        ),
        tests_path=tests_path,
        reference_path=reference_path,
        revision=2,
    )

    runner = _runner("privacy-probe", 2)
    capability = await runner.check_capability()
    if not capability.available:
        diagnostic = f"reason={capability.reason or 'unknown'} detail={capability.detail}"
        if os.getenv("CODEJUDGE_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker sandbox is required: {diagnostic}")
        pytest.skip(diagnostic)

    result = await runner.evaluate(task, _MALICIOUS_CANDIDATE)

    assert result.infrastructure_error is None
    assert result.sandbox_error is None
    assert result.passed == result.total == 1
    assert result.failed == 0


@pytest.mark.parametrize("task_id", ["frame-decoder", "retry-backoff", "ttl-cache"])
async def test_core_v4_revision_paths_and_private_material_are_not_visible(
    task_id: str,
) -> None:
    tasks = TaskRegistry.default()
    datasets = BenchmarkDatasetRegistry.default(tasks)
    core_v4 = datasets.get("codejudge-core", "4")
    task = datasets.resolve_dataset_task(core_v4, task_id)[1]
    assert task.revision == 2

    runner = _runner(task_id, 2)
    capability = await runner.check_capability()
    if not capability.available:
        diagnostic = f"reason={capability.reason or 'unknown'} detail={capability.detail}"
        if os.getenv("CODEJUDGE_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker sandbox is required: {diagnostic}")
        pytest.skip(diagnostic)

    result = await runner.evaluate(task, _MALICIOUS_CANDIDATE)

    assert result.infrastructure_error is None
    assert result.sandbox_error is None
    assert result.passed == result.total == 1
    assert result.failed == 0
