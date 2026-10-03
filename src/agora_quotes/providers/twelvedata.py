"""Twelve Data adapter (not implemented yet).

Planned: use the official ``twelvedata`` client with ``TWELVEDATA_API_KEY``
from the environment. Its websocket API is callback-based; ``stream`` will
bridge it into an async generator through an ``asyncio.Queue``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime

from agora_quotes.errors import AgoraQuotesError
from agora_quotes.models import Bar, Interval, Quote
from agora_quotes.symbols import Symbol


class TwelveDataProvider:
    name = "twelvedata"

    def get_quote(self, symbol: Symbol) -> Quote:
        raise NotImplementedError("Twelve Data adapter is not implemented yet")

    def get_quotes(self, symbols: Sequence[Symbol]) -> dict[Symbol, Quote | AgoraQuotesError]:
        raise NotImplementedError("Twelve Data adapter is not implemented yet")

    def get_history(
        self, symbol: Symbol, start: datetime, end: datetime, interval: Interval
    ) -> list[Bar]:
        raise NotImplementedError("Twelve Data adapter is not implemented yet")

    def stream(self, symbols: Sequence[Symbol]) -> AsyncIterator[Quote]:
        raise NotImplementedError("Twelve Data streaming is not implemented yet")
