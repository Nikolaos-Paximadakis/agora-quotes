"""Provider-independent data models.

All timestamps are timezone-aware UTC. Constructing a model with a naive
datetime raises ``ValueError``; aware datetimes in other zones are converted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

Interval = Literal["1m", "5m", "15m", "1h", "1d", "1wk", "1mo"]


def _as_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware, got naive {value!r}")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class Quote:
    """The latest known price of one instrument.

    ``timestamp`` is the market time the price refers to, as reported by the
    source. ``retrieved_at`` is when this package fetched it. ``delayed`` is
    True unless the source explicitly reports the data as real-time.
    """

    symbol: str
    price: float
    currency: str | None
    timestamp: datetime
    delayed: bool
    source: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        # Frozen dataclass: normalise through object.__setattr__.
        object.__setattr__(self, "timestamp", _as_utc(self.timestamp, "timestamp"))
        object.__setattr__(self, "retrieved_at", _as_utc(self.retrieved_at, "retrieved_at"))


@dataclass(frozen=True, slots=True)
class Bar:
    """One OHLCV bar. ``timestamp`` is the bar's start time; prices are unadjusted."""

    symbol: str
    timestamp: datetime
    interval: Interval
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _as_utc(self.timestamp, "timestamp"))
