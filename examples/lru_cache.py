"""Example solution for the bundled lru-cache task."""

from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._values: OrderedDict[int, int] = OrderedDict()

    def get(self, key: int) -> int:
        if key not in self._values:
            return -1
        self._values.move_to_end(key)
        return self._values[key]

    def put(self, key: int, value: int) -> None:
        if key in self._values:
            self._values.move_to_end(key)
        self._values[key] = value
        if len(self._values) > self._capacity:
            self._values.popitem(last=False)
