"""A thread-safe LRU cache bounded by the total weight of its values."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable, Generic, Hashable, TypeVar

V = TypeVar("V")


class LruCache(Generic[V]):
    def __init__(self, max_weight: int, weigh: Callable[[V], int]) -> None:
        if max_weight < 0:
            raise ValueError("max_weight must be non-negative")
        self._max_weight = max_weight
        self._weigh = weigh
        self._items: OrderedDict[Hashable, tuple[V, int]] = OrderedDict()
        self._weight = 0
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> V | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            self._items.move_to_end(key)
            return item[0]

    def put(self, key: Hashable, value: V) -> None:
        weight = self._weigh(value)
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._weight -= old[1]
            if weight > self._max_weight:
                return  # Never cacheable; do not evict everything for it.
            self._items[key] = (value, weight)
            self._weight += weight
            while self._weight > self._max_weight:
                _, (_, evicted) = self._items.popitem(last=False)
                self._weight -= evicted
