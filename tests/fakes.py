"""An in-memory Provider for testing the service layer."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from datetime import datetime, timezone

from agora_quotes import AgoraQuotesError, Bar, Interval, Quote, SymbolNotFound
from agora_quotes.symbols import Symbol

NOW = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)


class FakeProvider:
    def __init__(
        self, name: str, prices: dict[str, float], fail: AgoraQuotesError | None = None
    ) -> None:
        self.name = name
        self.prices = prices  # keyed by str(Symbol)
        self.fail = fail
        self.calls: list[tuple[str, list[str]]] = []

    def _quote(self, symbol: Symbol) -> Quote:
        if str(symbol) not in self.prices:
            raise SymbolNotFound(str(symbol), self.name)
        return Quote(str(symbol), self.prices[str(symbol)], "EUR", NOW, True, self.name, NOW)

    def get_quote(self, symbol: Symbol) -> Quote:
        self.calls.append(("get_quote", [str(symbol)]))
        if self.fail:
            raise self.fail
        return self._quote(symbol)

    def get_quotes(self, symbols: Sequence[Symbol]) -> dict[Symbol, Quote | AgoraQuotesError]:
        self.calls.append(("get_quotes", [str(s) for s in symbols]))
        if self.fail:
            raise self.fail
        results: dict[Symbol, Quote | AgoraQuotesError] = {}
        for s in symbols:
            try:
                results[s] = self._quote(s)
            except SymbolNotFound as e:
                results[s] = e
        return results

    def get_history(
        self, symbol: Symbol, start: datetime, end: datetime, interval: Interval
    ) -> list[Bar]:
        self.calls.append(("get_history", [str(symbol), start.isoformat(), end.isoformat()]))
        if self.fail:
            raise self.fail
        if str(symbol) not in self.prices:
            raise SymbolNotFound(str(symbol), self.name)
        p = self.prices[str(symbol)]
        return [Bar(str(symbol), start, interval, p, p, p, p, None, self.name)]


class FakeStreamer(FakeProvider):
    """A FakeProvider that can also stream; yields one quote per symbol."""

    async def stream(self, symbols: Sequence[Symbol]) -> AsyncGenerator[Quote, None]:
        self.calls.append(("stream", [str(s) for s in symbols]))
        try:
            for s in symbols:
                yield self._quote(s)
        finally:
            self.calls.append(("stream closed", []))
