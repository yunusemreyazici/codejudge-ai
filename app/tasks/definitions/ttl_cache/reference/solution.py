"""Trusted TTL cache oracle used only for sandboxed generated-test validation."""

from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity: int) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self._capacity = capacity
        self._items: OrderedDict[str, tuple[object, float]] = OrderedDict()

    def put(self, key: str, value: object, ttl: float, now: float) -> None:
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0:
            raise ValueError("ttl must be positive")
        self._purge(now)
        if key in self._items:
            del self._items[key]
        self._items[key] = (value, now + ttl)
        if len(self._items) > self._capacity:
            self._items.popitem(last=False)

    def get(self, key: str, now: float) -> object | None:
        self._purge(now)
        item = self._items.get(key)
        if item is None:
            return None
        self._items.move_to_end(key)
        return item[0]

    def delete(self, key: str, now: float) -> bool:
        self._purge(now)
        return self._items.pop(key, None) is not None

    def size(self, now: float) -> int:
        self._purge(now)
        return len(self._items)

    def _purge(self, now: float) -> None:
        expired = [key for key, (_, expires_at) in self._items.items() if expires_at <= now]
        for key in expired:
            del self._items[key]
