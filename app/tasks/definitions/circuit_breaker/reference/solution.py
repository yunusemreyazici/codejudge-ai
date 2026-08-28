"""Trusted circuit-breaker oracle used only for generated-test validation."""

from collections.abc import Callable
from typing import TypeVar

ResultT = TypeVar("ResultT")


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_timeout: float) -> None:
        if (
            isinstance(failure_threshold, bool)
            or not isinstance(failure_threshold, int)
            or failure_threshold <= 0
        ):
            raise ValueError("failure_threshold must be a positive integer")
        if (
            isinstance(recovery_timeout, bool)
            or not isinstance(recovery_timeout, (int, float))
            or recovery_timeout <= 0
        ):
            raise ValueError("recovery_timeout must be positive")
        self._threshold = failure_threshold
        self._recovery_timeout = float(recovery_timeout)
        self._state = "closed"
        self._failure_count = 0
        self._opened_at: float | None = None
        self._last_now: float | None = None

    @property
    def state(self) -> str:
        return self._state

    def call(self, operation: Callable[[], ResultT], now: float) -> ResultT:
        if self._last_now is not None and now < self._last_now:
            raise ValueError("timestamps must be nondecreasing")
        self._last_now = now
        if self._state == "open":
            assert self._opened_at is not None
            if now < self._opened_at + self._recovery_timeout:
                raise CircuitOpenError("circuit is open")
            self._state = "half_open"
        try:
            result = operation()
        except Exception:
            if self._state == "half_open":
                self._open(now)
            else:
                self._failure_count += 1
                if self._failure_count >= self._threshold:
                    self._open(now)
            raise
        self._state = "closed"
        self._failure_count = 0
        self._opened_at = None
        return result

    def reset(self) -> None:
        self._state = "closed"
        self._failure_count = 0
        self._opened_at = None
        self._last_now = None

    def _open(self, now: float) -> None:
        self._state = "open"
        self._opened_at = now
        self._failure_count = 0
