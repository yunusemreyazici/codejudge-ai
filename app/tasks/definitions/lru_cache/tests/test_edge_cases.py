from solution import LRUCache


def test_access_changes_recency() -> None:
    cache = LRUCache(2)
    cache.put(1, 10)
    cache.put(2, 20)
    assert cache.get(1) == 10
    cache.put(3, 30)
    assert cache.get(2) == -1
    assert cache.get(1) == 10


def test_capacity_one() -> None:
    cache = LRUCache(1)
    cache.put(1, 10)
    cache.put(2, 20)
    assert cache.get(1) == -1
    assert cache.get(2) == 20


def test_multiple_evictions() -> None:
    cache = LRUCache(2)
    cache.put(1, 10)
    cache.put(2, 20)
    cache.put(3, 30)
    cache.put(4, 40)
    assert cache.get(1) == -1
    assert cache.get(2) == -1
    assert cache.get(3) == 30
    assert cache.get(4) == 40


def test_update_refreshes_recency_without_growing_cache() -> None:
    cache = LRUCache(2)
    cache.put(1, 10)
    cache.put(2, 20)
    cache.put(1, 11)
    cache.put(3, 30)
    assert cache.get(1) == 11
    assert cache.get(2) == -1
