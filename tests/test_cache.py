import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agora_quotes import ConfigurationError, Quote
from agora_quotes.cache import SQLiteCache, TTLCache
from agora_quotes.symbols import parse


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_entries_expire_after_ttl() -> None:
    clock = Clock()
    cache: TTLCache[str, int] = TTLCache(10, clock)
    cache.set("a", 1)
    clock.now = 9.9
    assert cache.get("a") == 1
    clock.now = 10.0
    assert cache.get("a") is None


def test_missing_key() -> None:
    assert TTLCache[str, int](10).get("nope") is None


def test_zero_ttl_disables() -> None:
    cache: TTLCache[str, int] = TTLCache(0)
    cache.set("a", 1)
    assert cache.get("a") is None


def test_clear() -> None:
    cache: TTLCache[str, int] = TTLCache(10)
    cache.set("a", 1)
    cache.clear()
    assert cache.get("a") is None


def quote(symbol: str = "ATHEX:EXAE", price: float = 4.2) -> Quote:
    return Quote(
        symbol=symbol,
        price=price,
        currency="EUR",
        timestamp=datetime(2026, 10, 2, 14, 0, tzinfo=timezone(timedelta(hours=3))),
        delayed=True,
        source="yahoo",
        retrieved_at=datetime(2026, 10, 2, 11, 5, tzinfo=timezone.utc),
    )


def test_sqlite_round_trip_and_expiry(tmp_path: Path) -> None:
    clock = Clock()
    cache = SQLiteCache(tmp_path / "q.db", 10, clock)
    sym = parse("EXAE.AT")
    cache.set(sym, quote())
    clock.now = 9.9
    assert cache.get(sym) == quote()
    assert cache.get(parse("AAPL")) is None
    clock.now = 10.0
    assert cache.get(sym) is None


def test_sqlite_is_shared_between_instances(tmp_path: Path) -> None:
    # Two instances on one file stand in for two processes.
    path = tmp_path / "sub" / "q.db"
    writer = SQLiteCache(path, 60)
    reader = SQLiteCache(path, 5)
    writer.set(parse("AAPL"), quote("AAPL", 230.5))
    assert reader.get(parse("AAPL")) == quote("AAPL", 230.5)
    reader.clear()
    assert writer.get(parse("AAPL")) is None


def test_sqlite_set_purges_expired_rows(tmp_path: Path) -> None:
    clock = Clock()
    cache = SQLiteCache(tmp_path / "q.db", 10, clock)
    cache.set(parse("AAPL"), quote("AAPL"))
    clock.now = 20.0
    cache.set(parse("MSFT"), quote("MSFT"))
    with sqlite3.connect(tmp_path / "q.db") as db:
        assert db.execute("SELECT symbol FROM quotes").fetchall() == [("MSFT",)]


def test_sqlite_zero_ttl_does_not_store(tmp_path: Path) -> None:
    cache = SQLiteCache(tmp_path / "q.db", 0)
    cache.set(parse("AAPL"), quote("AAPL"))
    assert cache.get(parse("AAPL")) is None


def test_sqlite_unusable_path_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    with pytest.raises(ConfigurationError):
        SQLiteCache(blocker / "q.db", 60)


def test_sqlite_errors_after_open_are_misses(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "q.db"
    cache = SQLiteCache(path, 60)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE quotes")
    cache.set(parse("AAPL"), quote("AAPL"))
    assert cache.get(parse("AAPL")) is None
    assert "quote cache write failed" in caplog.text
    assert "quote cache read failed" in caplog.text
