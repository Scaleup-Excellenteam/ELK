"""Small bounded caches used by the online autocomplete service."""

from collections import OrderedDict
from threading import Lock
from typing import Generic, TypeVar


Key = TypeVar("Key")
Value = TypeVar("Value")


class LruCache(Generic[Key, Value]):
    """Thread-safe least-recently-used cache with a fixed item capacity."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")

        self._capacity = capacity
        self._entries: OrderedDict[Key, Value] = OrderedDict()
        self._lock = Lock()

    def get(self, key: Key) -> tuple[bool, Value | None]:
        """Return a cached value and mark it as recently used."""

        with self._lock:
            if key not in self._entries:
                return False, None

            value = self._entries.pop(key)
            self._entries[key] = value
            return True, value

    def put(self, key: Key, value: Value) -> None:
        """Insert a value and evict the least-recently-used item if needed."""

        with self._lock:
            self._entries.pop(key, None)
            self._entries[key] = value

            if len(self._entries) > self._capacity:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """Remove all cached entries."""

        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
