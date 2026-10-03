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


class TwelveDataProvider:
    name = "twelvedata"

    def get_quote(self, symbol: str) -> Quote:
        raise NotImplementedError("Twelve Data adapter is not implemented yet")

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote | AgoraQuotesError]:
        raise NotImplementedError("Twelve Data adapter is not implemented yet")

    def get_history(
        self, symbol: str, start: datetime, end: datetime | None, interval: Interval
    ) -> list[Bar]:
        raise NotImplementedError("Twelve Data adapter is not implemented yet")

    def stream(self, symbols: Sequence[str]) -> AsyncIterator[Quote]:
        raise NotImplementedError("Twelve Data streaming is not implemented yet")
