import pytest
from solution import CircuitBreaker, CircuitOpenError


def fail() -> None:
    raise RuntimeError("operation failed")


def test_recovery_boundary_allows_half_open_probe_and_success_closes() -> None:
    breaker = CircuitBreaker(1, 5)
    with pytest.raises(RuntimeError):
        breaker.call(fail, now=10)
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: None, now=14.999)
    observed: list[str] = []
    assert breaker.call(lambda: observed.append(breaker.state) or 42, now=15) == 42
    assert observed == ["half_open"]
    assert breaker.state == "closed"


def test_failed_half_open_probe_reopens_for_a_full_timeout() -> None:
    breaker = CircuitBreaker(1, 5)
    with pytest.raises(RuntimeError):
        breaker.call(fail, now=0)
    with pytest.raises(RuntimeError):
        breaker.call(fail, now=5)
    assert breaker.state == "open"
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "too early", now=9.999)
    assert breaker.call(lambda: "recovered", now=10) == "recovered"
    assert breaker.state == "closed"


def test_nonmonotonic_time_is_rejected_and_instances_are_independent() -> None:
    first = CircuitBreaker(2, 5)
    second = CircuitBreaker(2, 5)
    assert first.call(lambda: 1, now=10) == 1
    with pytest.raises(ValueError):
        first.call(lambda: 2, now=9)
    assert second.call(lambda: 3, now=-100) == 3
    assert second.state == "closed"


def test_reset_closes_breaker_and_clears_time_history() -> None:
    breaker = CircuitBreaker(1, 100)
    with pytest.raises(RuntimeError):
        breaker.call(fail, now=50)
    assert breaker.state == "open"
    breaker.reset()
    assert breaker.state == "closed"
    assert breaker.call(lambda: "ok", now=0) == "ok"
