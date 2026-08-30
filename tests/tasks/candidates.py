"""Deliberately flawed candidates used to prove portfolio tests are discriminating."""

INCORRECT_CANDIDATES = {
    "lru-cache": """
class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.items = {}

    def get(self, key):
        return self.items.get(key, -1)

    def put(self, key, value):
        self.items[key] = value
        if len(self.items) > self.capacity:
            self.items.pop(next(iter(self.items)))
""".lstrip(),
    "structured-event-parser": """
import json


def parse_events(lines):
    result = []
    for line in lines:
        if not line.strip():
            continue
        event = json.loads(line)
        event.setdefault("payload", {})
        result.append(event)
    return result
""".lstrip(),
    "interval-reservation": """
class ReservationBook:
    def __init__(self):
        self.items = {}

    def reserve(self, reservation_id, resource, start, end):
        for existing_id, (existing_resource, existing_start, existing_end) in self.items.items():
            endpoints_overlap = start in range(existing_start, existing_end) or end in range(
                existing_start, existing_end
            )
            if existing_resource == resource and endpoints_overlap:
                return False
        self.items[reservation_id] = (resource, start, end)
        return True

    def cancel(self, reservation_id):
        return self.items.pop(reservation_id, None) is not None

    def reservations(self, resource):
        return [
            {"id": reservation_id, "start": start, "end": end}
            for reservation_id, (item_resource, start, end) in self.items.items()
            if item_resource == resource
        ]
""".lstrip(),
    "config-layer-merge": """
def merge_config_layers(layers):
    result = {}
    for layer in layers:
        result.update(layer)
    return result
""".lstrip(),
    "logical-path": """
import posixpath


def normalize_path(path, cwd="/"):
    if path.startswith("/"):
        return posixpath.normpath(path)
    return posixpath.normpath(posixpath.join(cwd, path))
""".lstrip(),
    "frame-decoder": """
class LengthPrefixedDecoder:
    def __init__(self, max_frame_size):
        self.max_frame_size = max_frame_size

    def feed(self, chunk):
        prefix, payload = chunk.split(":", 1)
        length = int(prefix)
        if length > self.max_frame_size:
            raise ValueError("too large")
        return [payload[:length]]

    def finish(self):
        return None
""".lstrip(),
    "ttl-cache": """
class TTLCache:
    def __init__(self, capacity):
        self.values = {}

    def put(self, key, value, ttl, now):
        self.values[key] = value

    def get(self, key, now):
        return self.values.get(key)

    def delete(self, key, now):
        return self.values.pop(key, None) is not None

    def size(self, now):
        return len(self.values)
""".lstrip(),
    "rate-limiter": """
class SlidingWindowRateLimiter:
    def __init__(self, limit, window_seconds):
        self.limit = limit
        self.count = 0

    def allow(self, key, now):
        if self.count >= self.limit:
            return False
        self.count += 1
        return True
""".lstrip(),
    "retry-backoff": """
def retry_delay(attempt, base_delay, max_delay, multiplier=2.0):
    return base_delay
""".lstrip(),
    "dependency-resolver": """
class DependencyCycleError(ValueError):
    pass


def resolve_dependencies(graph):
    return sorted(graph)
""".lstrip(),
    "async-batch-processor": """
import asyncio


async def process_batch(items, worker, concurrency):
    tasks = [asyncio.create_task(worker(item)) for item in items]
    return [await completed for completed in asyncio.as_completed(tasks)]
""".lstrip(),
    "circuit-breaker": """
class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, failure_threshold, recovery_timeout):
        self.state = "closed"

    def call(self, operation, now):
        return operation()

    def reset(self):
        self.state = "closed"
""".lstrip(),
}
