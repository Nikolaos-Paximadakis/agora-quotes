from agora_quotes.cache import TTLCache


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
