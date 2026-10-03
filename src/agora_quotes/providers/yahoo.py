"""Yahoo Finance adapter, built on yfinance.

Symbols use Yahoo's format: Greek stocks take the ``.AT`` suffix (``EXAE.AT``),
US stocks are plain (``AAPL``). Yahoo data is for personal use only; see the
README for redistribution terms.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from agora_quotes.errors import AgoraQuotesError, ProviderError, RateLimited, SymbolNotFound
from agora_quotes.models import Bar, Interval, Quote


class YahooProvider:
    name = "yahoo"

    def get_quote(self, symbol: str) -> Quote:
        info = self._info(symbol)
        price = info.get("regularMarketPrice")
        market_time = info.get("regularMarketTime")
        if price is None or market_time is None:
            raise SymbolNotFound(symbol, self.name)
        delay = info.get("exchangeDataDelayedBy")
        return Quote(
            symbol=symbol,
            price=float(price),
            currency=info.get("currency"),
            timestamp=datetime.fromtimestamp(int(market_time), tz=timezone.utc),
            # Only an explicit 0 from Yahoo counts as real-time.
            delayed=delay != 0,
            source=self.name,
            retrieved_at=datetime.now(timezone.utc),
        )

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote | AgoraQuotesError]:
        results: dict[str, Quote | AgoraQuotesError] = {}
        for symbol in symbols:
            try:
                results[symbol] = self.get_quote(symbol)
            except SymbolNotFound as e:
                results[symbol] = e
        return results

    def get_history(
        self, symbol: str, start: datetime, end: datetime | None, interval: Interval
    ) -> list[Bar]:
        raise NotImplementedError("Yahoo history arrives in milestone 2")

    def _info(self, symbol: str) -> dict[str, Any]:
        try:
            info = yf.Ticker(symbol).info
        except YFRateLimitError as e:
            raise RateLimited(self.name) from e
        except Exception as e:  # yfinance raises many unrelated types
            raise ProviderError(f"{type(e).__name__}: {e}", self.name) from e
        return dict(info or {})
