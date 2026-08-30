from solution import TTLCache


def test_capacity_evicts_least_recently_used_live_entry() -> None:
    cache = TTLCache(2)
    cache.put("a", 1, ttl=100, now=0)
    cache.put("b", 2, ttl=100, now=1)
    assert cache.get("a", 2) == 1
    cache.put("c", 3, ttl=100, now=3)
    assert cache.get("b", 4) is None
    assert cache.get("a", 4) == 1
    assert cache.get("c", 4) == 3


def test_expired_entries_do_not_consume_capacity() -> None:
    cache = TTLCache(2)
    cache.put("expired", 1, ttl=1, now=0)
    cache.put("live", 2, ttl=10, now=0)
    cache.put("new", 3, ttl=10, now=2)
    assert cache.get("expired", 2) is None
    assert cache.get("live", 2) == 2
    assert cache.get("new", 2) == 3
    assert cache.size(2) == 2

    expired_mru = TTLCache(2)
    expired_mru.put("live", 1, ttl=20, now=0)
    expired_mru.put("expired", 2, ttl=1, now=0)
    expired_mru.put("new", 3, ttl=20, now=2)
    assert expired_mru.get("live", 2) == 1
    assert expired_mru.get("expired", 2) is None
    assert expired_mru.get("new", 2) == 3


def test_overwrite_refreshes_expiration_and_recency() -> None:
    cache = TTLCache(2)
    cache.put("a", 1, ttl=2, now=0)
    cache.put("b", 2, ttl=20, now=0)
    cache.put("a", 10, ttl=20, now=1)
    cache.put("c", 3, ttl=20, now=2)
    assert cache.get("a", 3) == 10
    assert cache.get("b", 3) is None
    assert cache.get("c", 3) == 3


def test_delete_is_idempotent_and_independent_instances_do_not_share_state() -> None:
    first = TTLCache(1)
    second = TTLCache(1)
    first.put("a", 1, ttl=10, now=0)
    assert second.get("a", 0) is None
    assert first.delete("a", 1) is True
    assert first.delete("a", 1) is False
    assert first.size(1) == 0

    expired = TTLCache(2)
    expired.put("stale", 1, ttl=1, now=0)
    expired.put("live", 2, ttl=10, now=0)
    assert expired.delete("stale", 1) is False
    assert expired.delete("missing", 1) is False
    assert expired.delete("live", 1) is True
    assert expired.delete("live", 1) is False
