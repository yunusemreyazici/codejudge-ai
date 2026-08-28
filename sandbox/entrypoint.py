"""Minimal supervisor for private host-side test orchestration.

The supervisor never receives test source or expected values. It accepts one bounded
case plan at a time, runs that plan in a fresh candidate-UID subprocess, and returns
only structured observations. Candidate processes cannot read the supervisor's host
protocol descriptor or future case plans because they run under a different UID.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

_MAX_LINE_BYTES = 64 * 1024
_CANDIDATE_UID = 10001
_CANDIDATE_GID = 10001


def _candidate_identity() -> None:
    os.setgroups([])
    os.setgid(_CANDIDATE_GID)
    os.setuid(_CANDIDATE_UID)


def _become_candidate() -> None:
    _candidate_identity()


def _response(request_id: object, **payload: object) -> dict[str, object]:
    return {"id": request_id, **payload}


def _run_case(request: dict[str, Any]) -> dict[str, object]:
    try:
        process = subprocess.Popen(
            [sys.executable, "/opt/codejudge/candidate_worker.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            preexec_fn=_candidate_identity,
        )
    except OSError:
        return _response(request.get("id"), ok=False, protocol_error="candidate_start_failed")
    if process.stdin is None or process.stdout is None:
        process.kill()
        process.wait()
        return _response(request.get("id"), ok=False, protocol_error="candidate_pipe_failed")

    outcomes: list[object] = []
    startup_error: object = None
    for step in request["steps"]:
        encoded = (
            json.dumps({"op": "step", "step": step}, separators=(",", ":")).encode("utf-8") + b"\n"
        )
        if len(encoded) > _MAX_LINE_BYTES:
            process.kill()
            process.wait()
            return _response(request.get("id"), ok=False, protocol_error="request_too_large")
        process.stdin.write(encoded)
        process.stdin.flush()
        line = process.stdout.readline(_MAX_LINE_BYTES + 1)
        if len(line) > _MAX_LINE_BYTES:
            process.kill()
            process.wait()
            return _response(request.get("id"), ok=False, protocol_error="response_too_large")
        try:
            raw: object = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            process.kill()
            process.wait()
            return _response(
                request.get("id"), ok=False, protocol_error="invalid_candidate_response"
            )
        if not isinstance(raw, dict):
            process.kill()
            process.wait()
            return _response(
                request.get("id"), ok=False, protocol_error="invalid_candidate_response"
            )
        if "startup_error" in raw:
            startup_error = raw["startup_error"]
            break
        outcomes.append(raw)

    try:
        process.stdin.write(b'{"op":"shutdown"}\n')
        process.stdin.flush()
    except BrokenPipeError:
        pass
    process.stdin.close()
    process.wait()
    candidate: dict[str, object]
    if startup_error is not None:
        candidate = {"startup_error": startup_error}
    else:
        candidate = {"outcomes": outcomes}
    return _response(request.get("id"), ok=True, candidate=candidate)


def main() -> int:
    if os.getenv("CODEJUDGE_GENERATED_PYTEST") == "1":
        _become_candidate()
        from pytest_entrypoint import main as pytest_main

        return pytest_main()

    for encoded in sys.stdin.buffer:
        if len(encoded) > _MAX_LINE_BYTES:
            response = _response(None, ok=False, protocol_error="request_too_large")
        else:
            try:
                request: object = json.loads(encoded)
            except json.JSONDecodeError:
                request = None
            if not isinstance(request, dict):
                response = _response(None, ok=False, protocol_error="invalid_request")
            elif request.get("op") == "shutdown":
                response = _response(request.get("id"), ok=True)
                print(json.dumps(response, separators=(",", ":")), flush=True)
                return 0
            elif request.get("op") == "run_case" and isinstance(request.get("steps"), list):
                response = _run_case(request)
            else:
                response = _response(request.get("id"), ok=False, protocol_error="unsupported_op")
        print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
