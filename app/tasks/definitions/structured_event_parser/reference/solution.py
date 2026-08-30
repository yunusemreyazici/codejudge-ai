"""Trusted structured-event parser oracle."""

import json


def parse_events(lines: list[str]) -> list[dict[str, object]]:
    if not isinstance(lines, list):
        raise TypeError("lines must be a list")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    previous_timestamp: int | None = None
    for line in lines:
        if not isinstance(line, str):
            raise TypeError("every line must be a string")
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("malformed JSON") from error
        if not isinstance(raw, dict):
            raise TypeError("each event must be an object")
        if set(raw) - {"id", "timestamp", "kind", "payload"}:
            raise ValueError("unknown event field")
        if not {"id", "timestamp", "kind"}.issubset(raw):
            raise ValueError("missing required event field")
        event_id = raw["id"]
        timestamp = raw["timestamp"]
        kind = raw["kind"]
        payload = raw.get("payload", {})
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("id must be a nonempty string")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
            raise ValueError("timestamp must be a nonnegative integer")
        if not isinstance(kind, str) or kind not in {"created", "updated", "deleted"}:
            raise ValueError("invalid event kind")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        if event_id in seen:
            raise ValueError("duplicate event id")
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise ValueError("timestamps must be nondecreasing")
        seen.add(event_id)
        previous_timestamp = timestamp
        result.append(
            {"id": event_id, "timestamp": timestamp, "kind": kind, "payload": payload.copy()}
        )
    return result
