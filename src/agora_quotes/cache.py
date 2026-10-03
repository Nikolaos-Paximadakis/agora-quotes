"""A small thread-safe in-memory TTL cache.

It lives inside one process: separate apps (processes) do not share it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """Entries expire ``ttl`` seconds after being set; ``ttl <= 0`` disables caching."""

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self._clock = clock
        self._data: dict[K, tuple[float, V]] = {}
        self._lock = threading.Lock()

    def get(self, key: K) -> V | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires, value = entry
            if self._clock() >= expires:
                del self._data[key]
                return None
            return value

    def set(self, key: K, value: V) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            self._data[key] = (self._clock() + self.ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
