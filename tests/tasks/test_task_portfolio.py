from __future__ import annotations

import pytest

from app.ai.service import reference_fingerprint
from app.runners.python_runner import PythonRunner
from app.runners.trusted_harness import OFFICIAL_CASES
from app.snapshots.fingerprints import task_fingerprint
from app.snapshots.fingerprints import tests_fingerprint as _tests_fingerprint
from app.tasks.registry import TaskRegistry
from tests.tasks.candidates import INCORRECT_CANDIDATES

NEW_TASK_IDS = tuple(INCORRECT_CANDIDATES)

EXPECTED_IDENTITIES = {
    "async-batch-processor": (
        "82129e90b0156a00461d1f69a9a7dcc83ca80c0c054a48d0037d47aa167bfedd",
        "98f1782f22c1a5341ae3aa30bb04c9befdaa93e974e6c471145836860577b9a1",
        "6e706f2ad62ac76552c8cd469e8c3f12bfa73e3e7ac463eb862adb81c43cc256",
    ),
    "circuit-breaker": (
        "21000a786964f16e0167b90bcc37f18f689311c95021ba73dbdcbc2437fbbad7",
        "64dcb0fea94ec7f0dccc74ee6bbc4319e06997c67d38670bab0f46ee41c37ed3",
        "caaa9f72ccdfac559898d68b9641b8f47a40b799145473707b34a8f5a9cb4224",
    ),
    "dependency-resolver": (
        "9446347072c3b10b3a8f468bd6484e6e7332ea9e4b434691271170d9500f458c",
        "cfcc510c677ebb4d90f49594f413803f8634dc01d4cb60c0362cce11e376e83b",
        "4d217704039907d817174e5719f642369bde4be3f9435958648c6dd353492cfb",
    ),
    "rate-limiter": (
        "cbfe634e3847d082e634a936b8d44eadddf4c88d2e1504596112c5a7f25bf54c",
        "8fa788f2335cb904651617584730b270fdf0622e778cc9793b571668f88b7be4",
        "abf2a1c7f00a3ab4e21e2ae0ab065b7ad539a6daf5d368f84145e840716eb401",
    ),
    "retry-backoff": (
        "f42dfd9fe12343865daff18202a010ab324c17f7f99b40c809b7bc64006c7567",
        "8ccf22ace44938c627a4ce842d91d27844b0ff1d66cd8bdd6b91564a3f47d99e",
        "0d9d98288b702880b77623d4f06a2af195c9cb7d8432ac92d4d3452e31912551",
    ),
    "ttl-cache": (
        "96bc0c57b021c274b0cdc8264daf6855e239172cb07ec919f9adc9621afe832c",
        "986a67c2492086473e206521b47a2dce9a82c81ab5509c322a4e7a10479722b3",
        "2ef80a5c74221d62e4c66e011128a2eec478737b45f25ff6741b3e8e5f7bd287",
    ),
}

EXPECTED_ENTRYPOINTS = {
    "async-batch-processor": "solution:process_batch",
    "circuit-breaker": "solution:CircuitBreaker",
    "dependency-resolver": "solution:resolve_dependencies",
    "rate-limiter": "solution:SlidingWindowRateLimiter",
    "retry-backoff": "solution:retry_delay",
    "ttl-cache": "solution:TTLCache",
}


@pytest.mark.parametrize("task_id", NEW_TASK_IDS)
def test_task_metadata_and_fingerprints_are_stable(task_id: str) -> None:
    task = TaskRegistry.default().get(task_id)
    tests_hash = _tests_fingerprint(task)
    tests_expected, task_expected, reference_expected = EXPECTED_IDENTITIES[task_id]

    assert task.specification.version == "1.0"
    assert task.specification.language == "python"
    assert task.specification.entrypoint == EXPECTED_ENTRYPOINTS[task_id]
    assert tests_hash == tests_expected
    assert task_fingerprint(task, tests_hash) == task_expected
    assert reference_fingerprint(task) == reference_expected


@pytest.mark.parametrize("task_id", NEW_TASK_IDS)
async def test_trusted_reference_passes_every_official_test(task_id: str) -> None:
    task = TaskRegistry.default().get(task_id)
    assert task.reference_path is not None

    result = await PythonRunner().evaluate(task, task.reference_path.read_text(encoding="utf-8"))

    assert result.infrastructure_error is None
    assert result.total == len(OFFICIAL_CASES[task_id])
    assert result.failed == 0
    assert result.passed == result.total


@pytest.mark.parametrize("task_id", NEW_TASK_IDS)
async def test_obviously_incorrect_candidate_is_rejected(task_id: str) -> None:
    task = TaskRegistry.default().get(task_id)

    result = await PythonRunner().evaluate(task, INCORRECT_CANDIDATES[task_id])

    assert result.infrastructure_error is None
    assert result.total > 0
    assert result.failed > 0
    assert result.passed < result.total
