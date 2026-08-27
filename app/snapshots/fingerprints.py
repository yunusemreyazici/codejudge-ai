"""Stable SHA-256 identities for source, tasks, tests, and runtime metadata."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Protocol

from app.snapshots.models import ExecutionEnvironmentSnapshot
from app.tasks.registry import RegisteredTask


def source_identity(source: str) -> tuple[str, int]:
    source_bytes = source.encode("utf-8")
    return hashlib.sha256(source_bytes).hexdigest(), len(source_bytes)


def tests_fingerprint(task: RegisteredTask) -> str:
    digest = hashlib.sha256()
    test_files = sorted(
        path
        for path in task.tests_path.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )
    for path in test_files:
        relative_name = path.relative_to(task.tests_path).as_posix().encode("utf-8")
        content = path.read_bytes()
        _update_framed(digest, relative_name)
        _update_framed(digest, content)
    return digest.hexdigest()


def task_fingerprint(task: RegisteredTask, test_identity: str | None = None) -> str:
    payload = {
        "task": task.specification.model_dump(mode="json"),
        "tests_fingerprint": test_identity or tests_fingerprint(task),
    }
    return _canonical_hash(payload)


def reproducibility_fingerprint(
    *,
    source_hash: str,
    task_hash: str,
    tests_hash: str,
    analyzer_versions: Mapping[str, str],
    scoring_policy_version: str,
    execution: ExecutionEnvironmentSnapshot,
    codejudge_version: str,
) -> str:
    return _canonical_hash(
        {
            "source_hash": source_hash,
            "task_fingerprint": task_hash,
            "tests_fingerprint": tests_hash,
            "analyzer_versions": dict(analyzer_versions),
            "scoring_policy_version": scoring_policy_version,
            "execution": execution.model_dump(mode="json"),
            "codejudge_version": codejudge_version,
        }
    )


def _canonical_hash(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


class _Digest(Protocol):
    def update(self, value: bytes) -> None: ...


def _update_framed(digest: _Digest, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, byteorder="big"))
    digest.update(value)
