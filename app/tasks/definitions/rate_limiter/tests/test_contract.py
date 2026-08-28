import pytest
from solution import SlidingWindowRateLimiter


def test_limit_is_enforced_and_rejected_attempt_is_not_recorded() -> None:
    limiter = SlidingWindowRateLimiter(2, 10)
    assert limiter.allow("user", 0) is True
    assert limiter.allow("user", 1) is True
    assert limiter.allow("user", 2) is False
    assert limiter.allow("user", 10) is True
    assert limiter.allow("user", 10.5) is False
    assert limiter.allow("user", 11) is True


def test_exact_window_boundary_is_expired() -> None:
    limiter = SlidingWindowRateLimiter(1, 5)
    assert limiter.allow("key", 10) is True
    assert limiter.allow("key", 14.999) is False
    assert limiter.allow("key", 15) is True


def test_invalid_configuration_is_rejected() -> None:
    for limit in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(limit, 1)
    for window in (0, -1, True):
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(1, window)
