"""Canonical Phase 7 benchmark identities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from app.benchmarks.models import BenchmarkModelRequest, DatasetTaskEntry


def canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def dataset_fingerprint(
    dataset_id: str, dataset_version: str, entries: Sequence[DatasetTaskEntry]
) -> str:
    return canonical_hash(
        {
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "tasks": [entry.model_dump(mode="json") for entry in sorted(entries, key=_task_key)],
        }
    )


def model_configuration_fingerprint(model: BenchmarkModelRequest, coding_prompt_hash: str) -> str:
    return canonical_hash(
        {
            "provider_id": model.provider_id,
            "model": model.model,
            "temperature": model.temperature,
            "top_p": model.top_p,
            "max_output_tokens": model.max_output_tokens,
            "seed": model.seed,
            "coding_prompt_hash": coding_prompt_hash,
        }
    )


def evaluator_fingerprint(identity: dict[str, Any]) -> str:
    return canonical_hash(identity)


def benchmark_run_fingerprint(
    *,
    dataset_hash: str,
    ordered_model_hashes: Sequence[str],
    samples_per_task: int,
    coding_prompt_version: str,
    coding_prompt_hash: str,
    evaluator_hash: str,
    policy_version: str,
) -> str:
    return canonical_hash(
        {
            "dataset_fingerprint": dataset_hash,
            "model_configuration_fingerprints": list(ordered_model_hashes),
            "samples_per_task": samples_per_task,
            "coding_prompt_version": coding_prompt_version,
            "coding_prompt_hash": coding_prompt_hash,
            "evaluator_fingerprint": evaluator_hash,
            "benchmark_policy_version": policy_version,
        }
    )


def request_fingerprint(payload: object) -> str:
    return canonical_hash(payload)


def _task_key(entry: DatasetTaskEntry) -> tuple[str, str]:
    return entry.task_id, entry.task_version
