"""Test-only synthetic immutable task revisions."""

from __future__ import annotations

import json
from pathlib import Path

from app.tasks.registry import TaskRegistry


def build_revision_registry(
    root: Path,
    *,
    default_revision: int = 2,
) -> TaskRegistry:
    definitions = root / "definitions"
    _write_revision(definitions / "sample", title="Sample revision one", version="1.0")
    _write_revision(
        definitions / "sample" / "revisions" / "2",
        title="Sample revision two",
        version="2.0",
    )
    registry = TaskRegistry(
        definitions,
        default_revisions={"sample": default_revision},
    )
    registry.load()
    return registry


def _write_revision(directory: Path, *, title: str, version: str) -> None:
    tests = directory / "tests"
    reference = directory / "reference"
    tests.mkdir(parents=True)
    reference.mkdir()
    definition = {
        "id": "sample",
        "title": title,
        "description": f"Return the {title.lower()} marker.",
        "language": "python",
        "entrypoint": "solution:marker",
        "timeout_seconds": 1,
        "version": version,
    }
    (directory / "task.yaml").write_text(json.dumps(definition), encoding="utf-8")
    (tests / "test_sample.py").write_text(
        f'from solution import marker\n\ndef test_marker(): assert marker() == "{version}"\n',
        encoding="utf-8",
    )
    (reference / "solution.py").write_text(
        f'def marker():\n    return "{version}"\n',
        encoding="utf-8",
    )
