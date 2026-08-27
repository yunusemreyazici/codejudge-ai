"""Canonical request identity for explicit HTTP idempotency."""

from __future__ import annotations

import hashlib
import json

from app.evaluator.models import EvaluationRequest
from app.snapshots.fingerprints import source_identity


def request_fingerprint(request: EvaluationRequest) -> str:
    source_hash, _ = source_identity(request.code)
    payload = {
        "task_id": request.task_id,
        "language": request.language,
        "source_hash": source_hash,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
