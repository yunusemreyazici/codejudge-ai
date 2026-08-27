from solution import LRUCache


def test_basic_insertion_and_retrieval() -> None:
    cache = LRUCache(2)
    cache.put(1, 10)
    assert cache.get(1) == 10


def test_missing_key_returns_negative_one() -> None:
    assert LRUCache(2).get(99) == -1


def test_updating_existing_key() -> None:
    cache = LRUCache(2)
    cache.put(1, 10)
    cache.put(1, 20)
    assert cache.get(1) == 20


def test_least_recently_used_key_is_evicted() -> None:
    cache = LRUCache(2)
    cache.put(1, 10)
    cache.put(2, 20)
    cache.put(3, 30)
    assert cache.get(1) == -1
    assert cache.get(2) == 20
    assert cache.get(3) == 30
