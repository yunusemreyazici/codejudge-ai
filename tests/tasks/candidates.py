"""Deliberately flawed candidates used to prove portfolio tests are discriminating."""

INCORRECT_CANDIDATES = {
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
