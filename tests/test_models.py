from datetime import datetime, timedelta, timezone

import pytest

from agora_quotes import Bar, Quote

UTC = timezone.utc
ATHENS = timezone(timedelta(hours=3))


def make_quote(**overrides: object) -> Quote:
    fields: dict[str, object] = {
        "symbol": "EXAE.AT",
        "price": 10.22,
        "currency": "EUR",
        "timestamp": datetime(2026, 10, 2, 14, 0, tzinfo=UTC),
        "delayed": True,
        "source": "test",
        "retrieved_at": datetime(2026, 10, 2, 14, 15, tzinfo=UTC),
    }
    fields.update(overrides)
    return Quote(**fields)  # type: ignore[arg-type]


def test_quote_converts_aware_timestamps_to_utc() -> None:
    q = make_quote(timestamp=datetime(2026, 10, 2, 17, 0, tzinfo=ATHENS))
    assert q.timestamp == datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
    assert q.timestamp.tzinfo is UTC


@pytest.mark.parametrize("field", ["timestamp", "retrieved_at"])
def test_quote_rejects_naive_datetimes(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        make_quote(**{field: datetime(2026, 10, 2, 14, 0)})


def test_quote_is_frozen() -> None:
    q = make_quote()
    with pytest.raises(AttributeError):
        q.price = 1.0  # type: ignore[misc]


def test_bar_converts_and_rejects_naive() -> None:
    bar = Bar("AAPL", datetime(2026, 1, 2, 9, 30, tzinfo=ATHENS), "1d", 1, 2, 0.5, 1.5, 100, "t")
    assert bar.timestamp.tzinfo is UTC
    with pytest.raises(ValueError):
        Bar("AAPL", datetime(2026, 1, 2), "1d", 1, 2, 0.5, 1.5, None, "t")
