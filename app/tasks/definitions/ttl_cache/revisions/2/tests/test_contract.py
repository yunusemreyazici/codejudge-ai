import pytest
from solution import TTLCache


def test_put_get_overwrite_and_missing_values() -> None:
    cache = TTLCache(2)
    marker = object()
    assert cache.get("missing", 0) is None
    cache.put("a", marker, ttl=5, now=0)
    assert cache.get("a", 1) is marker
    cache.put("a", "new", ttl=10, now=2)
    assert cache.get("a", 11.9) == "new"
    assert cache.size(11.9) == 1


def test_expiration_uses_inclusive_boundary_and_removes_stale_data() -> None:
    cache = TTLCache(2)
    cache.put("a", 1, ttl=5, now=10)
    assert cache.get("a", 14.999) == 1
    assert cache.get("a", 15) is None
    assert cache.size(15) == 0
    assert cache.delete("a", 15) is False


def test_invalid_capacity_and_ttl_are_rejected() -> None:
    for capacity in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            TTLCache(capacity)
    cache = TTLCache(1)
    for ttl in (0, -0.1, True):
        with pytest.raises(ValueError):
            cache.put("a", 1, ttl=ttl, now=0)
