from pathlib import Path

from app.evaluator.models import Task
from app.snapshots.fingerprints import (
    reproducibility_fingerprint,
    source_identity,
    task_fingerprint,
)
from app.snapshots.fingerprints import tests_fingerprint as calculate_tests_fingerprint
from app.snapshots.models import ExecutionEnvironmentSnapshot
from app.tasks.registry import RegisteredTask


def _task(tests_path: Path, *, version: str = "1.0") -> RegisteredTask:
    return RegisteredTask(
        specification=Task(
            id="fingerprint-task",
            title="Fingerprint",
            description="Fingerprint fixture",
            language="python",
            timeout_seconds=1,
            version=version,
        ),
        tests_path=tests_path,
    )


def test_source_identity_hashes_exact_utf8_bytes() -> None:
    source = "value = 'é'\n"

    first = source_identity(source)

    assert first == source_identity(source)
    assert first[0] != source_identity(source + "\n")[0]
    assert first[1] == len(source.encode("utf-8"))


def test_test_and_task_fingerprints_change_with_test_content(tmp_path: Path) -> None:
    test_file = tmp_path / "test_candidate.py"
    test_file.write_text("def test_value(): assert True\n", encoding="utf-8")
    task = _task(tmp_path)
    original_tests = calculate_tests_fingerprint(task)
    original_task = task_fingerprint(task, original_tests)

    test_file.write_text("def test_value(): assert False\n", encoding="utf-8")
    changed_tests = calculate_tests_fingerprint(task)

    assert changed_tests != original_tests
    assert task_fingerprint(task, changed_tests) != original_task


def test_task_fingerprint_uses_versioned_public_metadata(tmp_path: Path) -> None:
    (tmp_path / "test_candidate.py").write_text("def test_value(): pass\n", encoding="utf-8")

    assert task_fingerprint(_task(tmp_path, version="1.0")) != task_fingerprint(
        _task(tmp_path, version="2.0")
    )


def test_reproducibility_fingerprint_is_canonical_and_sensitive() -> None:
    execution = ExecutionEnvironmentSnapshot(
        backend="docker",
        sandbox_image="sandbox:phase2",
        sandbox_image_id="sha256:image",
    )
    base = {
        "source_hash": "a" * 64,
        "task_hash": "b" * 64,
        "tests_hash": "c" * 64,
        "analyzer_versions": {"ruff": "1", "mypy": "2"},
        "scoring_policy_version": "1",
        "execution": execution,
        "codejudge_version": "0.4.0",
    }
    original = reproducibility_fingerprint(**base)

    assert original == reproducibility_fingerprint(
        **{**base, "analyzer_versions": {"mypy": "2", "ruff": "1"}}
    )
    changes = [
        {"source_hash": "d" * 64},
        {"task_hash": "d" * 64},
        {"scoring_policy_version": "2"},
        {"analyzer_versions": {"ruff": "9"}},
        {
            "execution": ExecutionEnvironmentSnapshot(
                backend="docker",
                sandbox_image="sandbox:phase2",
                sandbox_image_id="sha256:changed",
            )
        },
    ]
    for change in changes:
        assert reproducibility_fingerprint(**{**base, **change}) != original
