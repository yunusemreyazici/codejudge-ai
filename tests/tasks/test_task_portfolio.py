from __future__ import annotations

import pytest

from app.ai.service import reference_fingerprint
from app.runners.python_runner import PythonRunner
from app.runners.trusted_harness import OFFICIAL_CASES
from app.snapshots.fingerprints import task_fingerprint
from app.snapshots.fingerprints import tests_fingerprint as _tests_fingerprint
from app.tasks.registry import TaskRegistry
from tests.tasks.candidates import INCORRECT_CANDIDATES

INCORRECT_TASK_IDS = tuple(INCORRECT_CANDIDATES)
ALL_TASK_IDS = tuple(task.id for task in TaskRegistry.default().list())

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
    "config-layer-merge": (
        "449c009c4fd9f63612ec0cc1eb2306df17cd3921cc05a77ecb1848c9139fd294",
        "174aff54c17a312cf7d8c887feb49807a60a8f638cd7a4e092573116ca242c3c",
        "13f39ac6e4c87f95291a8b64d4a45e36e1025a2af2fa64f4baf9e1cf9f0d9459",
    ),
    "dependency-resolver": (
        "9446347072c3b10b3a8f468bd6484e6e7332ea9e4b434691271170d9500f458c",
        "cfcc510c677ebb4d90f49594f413803f8634dc01d4cb60c0362cce11e376e83b",
        "4d217704039907d817174e5719f642369bde4be3f9435958648c6dd353492cfb",
    ),
    "frame-decoder": (
        "04a2163e2182da736e1a1e7c3e96840f9e2cf38239704ff77850f1092dd920e0",
        "ebab75405abb861ff2970ce786c7af2d82aad56dc67d6d0169ef529e1652e613",
        "d8dc92dac390be11de1472c85c7082b532ccc44e625cf0da7e5f7635bba38ab9",
    ),
    "interval-reservation": (
        "0d775029df37dc731164c5b3adb0fa408a23ea7fb88ad5067fc40cb46bde30f9",
        "bf432b76f3feb3434628347072c61a0996441d33d7048a18865596de1225c125",
        "4be5d1a3735896c228cac4e3d479968d44f93a7352e7ea8cc2b67a17516815c8",
    ),
    "logical-path": (
        "4831548979a378974077e835548a284488494a255344cfdd88f8fd51ce6d6d9b",
        "e04fcf94a03b5b488352d7df9785f6bd7fcdc378fddffc4c129ae4c6fd7af6ad",
        "efcac7dd1e37c0aef9a3bd6fb3ecb3e932474b666e189086770fa2387e6c7e7e",
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
    "structured-event-parser": (
        "be0c453b8a2e43b47155c273c790fc008f6adbb2d40c268aececbc49b992fd4f",
        "6c6e85c8b155c7ea010c9cd649a6f319f6af79b014973a5f652fdd7d5fe83031",
        "941d2305d8aee98f5819ca9ae7865805fd6ed562a82bc12c890667ab68cc4aeb",
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
    "config-layer-merge": "solution:merge_config_layers",
    "dependency-resolver": "solution:resolve_dependencies",
    "frame-decoder": "solution:LengthPrefixedDecoder",
    "interval-reservation": "solution:ReservationBook",
    "logical-path": "solution:normalize_path",
    "rate-limiter": "solution:SlidingWindowRateLimiter",
    "retry-backoff": "solution:retry_delay",
    "structured-event-parser": "solution:parse_events",
    "ttl-cache": "solution:TTLCache",
}


@pytest.mark.parametrize("task_id", INCORRECT_TASK_IDS)
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


@pytest.mark.parametrize("task_id", ALL_TASK_IDS)
async def test_trusted_reference_passes_every_official_test(task_id: str) -> None:
    task = TaskRegistry.default().get(task_id)
    assert task.reference_path is not None

    result = await PythonRunner().evaluate(task, task.reference_path.read_text(encoding="utf-8"))

    assert result.infrastructure_error is None
    assert result.total == len(OFFICIAL_CASES[task_id])
    assert result.failed == 0
    assert result.passed == result.total


@pytest.mark.parametrize("task_id", INCORRECT_TASK_IDS)
async def test_obviously_incorrect_candidate_is_rejected(task_id: str) -> None:
    task = TaskRegistry.default().get(task_id)

    result = await PythonRunner().evaluate(task, INCORRECT_CANDIDATES[task_id])

    assert result.infrastructure_error is None
    assert result.total > 0
    assert result.failed > 0
    assert result.passed < result.total
