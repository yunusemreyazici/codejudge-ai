"""Trusted LRU cache oracle used only for sandboxed generated-test validation."""

from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._items: OrderedDict[int, int] = OrderedDict()

    def get(self, key: int) -> int:
        if key not in self._items:
            return -1
        self._items.move_to_end(key)
        return self._items[key]

    def put(self, key: int, value: int) -> None:
        if key in self._items:
            self._items.move_to_end(key)
        self._items[key] = value
        if len(self._items) > self._capacity:
            self._items.popitem(last=False)
