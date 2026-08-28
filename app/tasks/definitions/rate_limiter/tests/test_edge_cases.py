import pytest
from solution import SlidingWindowRateLimiter


def test_keys_have_independent_windows() -> None:
    limiter = SlidingWindowRateLimiter(1, 10)
    assert limiter.allow("a", 0) is True
    assert limiter.allow("a", 1) is False
    assert limiter.allow("b", 1) is True
    assert limiter.allow("b", 2) is False


def test_prunes_multiple_old_events_without_dropping_live_events() -> None:
    limiter = SlidingWindowRateLimiter(3, 10)
    assert limiter.allow("key", 0) is True
    assert limiter.allow("key", 1) is True
    assert limiter.allow("key", 9) is True
    assert limiter.allow("key", 10) is True
    assert limiter.allow("key", 10.5) is False
    assert limiter.allow("key", 11) is True
    assert limiter.allow("key", 19) is True


def test_nonmonotonic_time_is_rejected_per_key_only() -> None:
    limiter = SlidingWindowRateLimiter(2, 10)
    assert limiter.allow("a", 5) is True
    with pytest.raises(ValueError):
        limiter.allow("a", 4.9)
    assert limiter.allow("b", -100) is True
    assert limiter.allow("a", 5) is True


def test_instances_do_not_share_state() -> None:
    first = SlidingWindowRateLimiter(1, 10)
    second = SlidingWindowRateLimiter(1, 10)
    assert first.allow("same", 0) is True
    assert first.allow("same", 1) is False
    assert second.allow("same", 1) is True
