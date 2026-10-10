from datetime import date, datetime, timezone

import pytest

from agora_quotes.providers.base import bar_dates

UTC = timezone.utc


@pytest.mark.parametrize(
    ("start", "end", "interval", "expected"),
    [
        pytest.param(
            datetime(2026, 1, 15, tzinfo=UTC),
            datetime(2026, 1, 16, tzinfo=UTC),
            "1d",
            (date(2026, 1, 15), date(2026, 1, 15)),
            id="one-day",
        ),
        pytest.param(
            datetime(2026, 1, 15, tzinfo=UTC),
            datetime(2026, 1, 16, 14, tzinfo=UTC),
            "1d",
            (date(2026, 1, 15), date(2026, 1, 16)),
            id="end-after-midnight-includes-that-day",
        ),
        pytest.param(
            datetime(2026, 1, 15, tzinfo=UTC),  # a Thursday
            datetime(2026, 1, 17, tzinfo=UTC),
            "1wk",
            (date(2026, 1, 12), date(2026, 1, 16)),
            id="week-back-to-monday",
        ),
        pytest.param(
            datetime(2026, 1, 15, tzinfo=UTC),
            datetime(2026, 3, 1, tzinfo=UTC),
            "1mo",
            (date(2026, 1, 1), date(2026, 2, 28)),
            id="month-back-to-the-1st",
        ),
    ],
)
def test_bar_dates(
    start: datetime, end: datetime, interval: str, expected: tuple[date, date]
) -> None:
    assert bar_dates(start, end, interval) == expected  # type: ignore[arg-type]
