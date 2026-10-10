"""The contracts every data-source adapter implements."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from datetime import date, datetime, timedelta
from typing import Protocol, runtime_checkable

from agora_quotes.errors import AgoraQuotesError
from agora_quotes.models import Bar, Interval, Quote
from agora_quotes.symbols import Symbol


def bar_dates(start: datetime, end: datetime, interval: Interval) -> tuple[date, date]:
    """The inclusive trading-date range of a daily-or-longer history request.

    From ``start``'s date to the date of the last instant before ``end`` (so an
    ``end`` after midnight includes that day). For ``1wk``/``1mo`` the first
    date moves back to the start of its week or month: a period bar is dated
    by its first day, so the period containing ``start`` would otherwise be
    dropped. Adapters send these dates upstream and keep only bars inside them.
    """
    first, last = start.date(), (end - timedelta(microseconds=1)).date()
    if interval == "1wk":
        first -= timedelta(days=first.weekday())
    elif interval == "1mo":
        first = first.replace(day=1)
    return first, last


@runtime_checkable
class Provider(Protocol):
    """A synchronous source of quotes and history.

    Symbols arrive already parsed; each provider converts them to its native
    format and sets ``Quote.symbol``/``Bar.symbol`` to ``str(symbol)``.

    Implementations must raise only ``AgoraQuotesError`` subclasses, never
    vendor exceptions, and must set ``Quote.delayed`` to False only when the
    source explicitly says the data is real-time.
    """

    name: str

    def get_quote(self, symbol: Symbol) -> Quote: ...

    def get_quotes(self, symbols: Sequence[Symbol]) -> dict[Symbol, Quote | AgoraQuotesError]:
        """Per-symbol failures are returned as values; source-wide failures raise."""
        ...

    def get_history(
        self, symbol: Symbol, start: datetime, end: datetime, interval: Interval
    ) -> list[Bar]:
        """Bars in ``[start, end)`` (both UTC).

        Intraday bars are compared by instant: ``start <= timestamp < end``.
        Daily and longer bars are compared by trading date in exchange time,
        within ``bar_dates(start, end, interval)``.
        """
        ...


@runtime_checkable
class StreamingProvider(Protocol):
    """A provider that can push quotes as they change.

    ``stream`` is an async generator: consume it with ``async for`` and cancel
    the task to stop. Callback-based vendor SDKs bridge in via an
    ``asyncio.Queue`` fed with ``loop.call_soon_threadsafe``.
    """

    name: str

    def stream(self, symbols: Sequence[Symbol]) -> AsyncGenerator[Quote, None]: ...
