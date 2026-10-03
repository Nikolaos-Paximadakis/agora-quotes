"""Quote caches with a TTL.

``TTLCache`` lives inside one process: separate apps (processes) do not share
it. ``SQLiteCache`` keeps quotes in a SQLite file that several processes can
share.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Hashable
from datetime import datetime
from pathlib import Path
from typing import Generic, TypeVar

from agora_quotes.errors import ConfigurationError
from agora_quotes.models import Quote
from agora_quotes.symbols import Symbol

log = logging.getLogger(__name__)

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


class SQLiteCache:
    """A ``Symbol -> Quote`` cache in a SQLite file, shared by every process using it.

    Expiry times are wall-clock (``time.time``) so they mean the same in every
    process; each process applies its own ``ttl`` to the entries it writes.
    Database errors after construction are logged and treated as a cache miss,
    so a busy or broken cache never fails a quote request.
    """

    def __init__(
        self, path: str | os.PathLike[str], ttl: float, clock: Callable[[], float] = time.time
    ) -> None:
        self.ttl = ttl
        self.path = Path(path).expanduser()
        self._clock = clock
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS quotes"
                    " (symbol TEXT PRIMARY KEY, expires REAL NOT NULL, quote TEXT NOT NULL)"
                )
        except (OSError, sqlite3.Error) as e:
            raise ConfigurationError(f"cannot open quote cache {self.path}: {e}") from e

    def get(self, key: Symbol) -> Quote | None:
        try:
            with self._connect() as db:
                row = db.execute(
                    "SELECT quote FROM quotes WHERE symbol = ? AND expires > ?",
                    (str(key), self._clock()),
                ).fetchone()
        except sqlite3.Error as e:
            log.warning("quote cache read failed (%s): %s", self.path, e)
            return None
        return None if row is None else _load(row[0])

    def set(self, key: Symbol, value: Quote) -> None:
        if self.ttl <= 0:
            return
        now = self._clock()
        try:
            with self._connect() as db:
                db.execute("DELETE FROM quotes WHERE expires <= ?", (now,))
                db.execute(
                    "INSERT OR REPLACE INTO quotes VALUES (?, ?, ?)",
                    (str(key), now + self.ttl, _dump(value)),
                )
        except sqlite3.Error as e:
            log.warning("quote cache write failed (%s): %s", self.path, e)

    def clear(self) -> None:
        """Empty the cache for every process that shares the file."""
        with self._connect() as db:
            db.execute("DELETE FROM quotes")

    def _connect(self) -> _Connection:
        return _Connection(self.path)


class _Connection:
    """Open a connection per operation and close it afterwards.

    ``sqlite3.Connection`` as a context manager commits but does not close.
    """

    def __init__(self, path: Path) -> None:
        self._db = sqlite3.connect(path, timeout=5)

    def __enter__(self) -> sqlite3.Connection:
        return self._db

    def __exit__(self, exc_type: object, *rest: object) -> None:
        try:
            if exc_type is None:
                self._db.commit()
        finally:
            self._db.close()


def _dump(quote: Quote) -> str:
    return json.dumps(
        {
            "symbol": quote.symbol,
            "price": quote.price,
            "currency": quote.currency,
            "timestamp": quote.timestamp.isoformat(),
            "delayed": quote.delayed,
            "source": quote.source,
            "retrieved_at": quote.retrieved_at.isoformat(),
        }
    )


def _load(raw: str) -> Quote:
    d = json.loads(raw)
    return Quote(
        symbol=d["symbol"],
        price=d["price"],
        currency=d["currency"],
        timestamp=datetime.fromisoformat(d["timestamp"]),
        delayed=d["delayed"],
        source=d["source"],
        retrieved_at=datetime.fromisoformat(d["retrieved_at"]),
    )
