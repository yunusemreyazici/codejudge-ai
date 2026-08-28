"""Trusted sliding-window limiter oracle for generated-test validation."""

from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        if (
            isinstance(window_seconds, bool)
            or not isinstance(window_seconds, (int, float))
            or window_seconds <= 0
        ):
            raise ValueError("window_seconds must be positive")
        self._limit = limit
        self._window = float(window_seconds)
        self._events: defaultdict[str, deque[float]] = defaultdict(deque)
        self._last_call: dict[str, float] = {}

    def allow(self, key: str, now: float) -> bool:
        previous = self._last_call.get(key)
        if previous is not None and now < previous:
            raise ValueError("timestamps must be nondecreasing per key")
        self._last_call[key] = now
        events = self._events[key]
        boundary = now - self._window
        while events and events[0] <= boundary:
            events.popleft()
        if len(events) >= self._limit:
            return False
        events.append(now)
        return True
