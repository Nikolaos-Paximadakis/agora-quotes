"""The contracts every data-source adapter implements."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from agora_quotes.errors import AgoraQuotesError
from agora_quotes.models import Bar, Interval, Quote
from agora_quotes.symbols import Symbol


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
        """Bars with ``start <= timestamp < end`` (both UTC)."""
        ...


@runtime_checkable
class StreamingProvider(Protocol):
    """A provider that can push quotes as they change.

    ``stream`` is an async generator: consume it with ``async for`` and cancel
    the task to stop. Callback-based vendor SDKs bridge in via an
    ``asyncio.Queue`` fed with ``loop.call_soon_threadsafe``.
    """

    name: str

    def stream(self, symbols: Sequence[Symbol]) -> AsyncIterator[Quote]: ...
