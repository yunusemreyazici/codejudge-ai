import pytest
from solution import CircuitBreaker, CircuitOpenError


def fail() -> None:
    raise RuntimeError("operation failed")


def test_closed_calls_return_values_and_success_resets_failure_count() -> None:
    breaker = CircuitBreaker(2, 10)
    with pytest.raises(RuntimeError):
        breaker.call(fail, now=0)
    assert breaker.state == "closed"
    assert breaker.call(lambda: "ok", now=1) == "ok"
    with pytest.raises(RuntimeError):
        breaker.call(fail, now=2)
    assert breaker.state == "closed"


def test_threshold_opens_and_blocks_without_invoking_operation() -> None:
    breaker = CircuitBreaker(2, 10)
    for now in (0, 1):
        with pytest.raises(RuntimeError):
            breaker.call(fail, now=now)
    assert breaker.state == "open"
    invoked = False

    def forbidden() -> None:
        nonlocal invoked
        invoked = True

    with pytest.raises(CircuitOpenError):
        breaker.call(forbidden, now=10.999)
    assert invoked is False
    assert breaker.state == "open"


def test_invalid_configuration_is_rejected() -> None:
    for threshold in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            CircuitBreaker(threshold, 1)
    for timeout in (0, -1, True):
        with pytest.raises(ValueError):
            CircuitBreaker(1, timeout)
