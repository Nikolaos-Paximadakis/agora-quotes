"""Yahoo Finance adapter, built on yfinance.

Yahoo symbols are the ticker plus an exchange suffix from
``symbols.YAHOO_SUFFIXES`` (``ATHEX:EXAE`` -> ``EXAE.AT``; US tickers have no
suffix). Yahoo data is for personal use only; see the README for
redistribution terms.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any

import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from agora_quotes.errors import AgoraQuotesError, ProviderError, RateLimited, SymbolNotFound
from agora_quotes.models import Bar, Interval, Quote
from agora_quotes.symbols import YAHOO_SUFFIXES, Symbol

# Yahoo only serves intraday bars this far back; older requests come back
# empty with no error, so they are rejected up front instead.
INTRADAY_MAX_AGE: dict[str, timedelta] = {
    "1m": timedelta(days=30),
    "5m": timedelta(days=60),
    "15m": timedelta(days=60),
    "1h": timedelta(days=730),
}


# yfinance logs failures (e.g. the 404 for an unknown ticker) on its "yfinance"
# logger even though the adapter already turns them into exceptions. The filter
# drops those records only while an adapter call is running, so apps using
# yfinance directly, or turning on its debug logging, still see everything.
_quiet: ContextVar[bool] = ContextVar("agora_quotes_quiet_yfinance", default=False)


class _QuietFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not _quiet.get() or record.levelno >= logging.CRITICAL:
            return True
        level = logging.getLogger("yfinance").level
        return logging.NOTSET < level <= logging.DEBUG


_QUIET_FILTER = _QuietFilter()


@contextmanager
def _quiet_yfinance() -> Iterator[None]:
    logger = logging.getLogger("yfinance")
    if _QUIET_FILTER not in logger.filters:
        logger.addFilter(_QUIET_FILTER)
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)


def yahoo_symbol(symbol: Symbol) -> str:
    return symbol.ticker + YAHOO_SUFFIXES.get(symbol.exchange or "", "")


class YahooProvider:
    name = "yahoo"

    def get_quote(self, symbol: Symbol) -> Quote:
        info = self._info(symbol)
        price = info.get("regularMarketPrice")
        market_time = info.get("regularMarketTime")
        if price is None or market_time is None:
            raise SymbolNotFound(str(symbol), self.name)
        delay = info.get("exchangeDataDelayedBy")
        return Quote(
            symbol=str(symbol),
            price=float(price),
            currency=info.get("currency"),
            timestamp=datetime.fromtimestamp(int(market_time), tz=timezone.utc),
            # Only an explicit 0 from Yahoo counts as real-time.
            delayed=delay != 0,
            source=self.name,
            retrieved_at=datetime.now(timezone.utc),
        )

    def get_quotes(self, symbols: Sequence[Symbol]) -> dict[Symbol, Quote | AgoraQuotesError]:
        results: dict[Symbol, Quote | AgoraQuotesError] = {}
        for symbol in symbols:
            try:
                results[symbol] = self.get_quote(symbol)
            except SymbolNotFound as e:
                results[symbol] = e
        return results

    def get_history(
        self, symbol: Symbol, start: datetime, end: datetime, interval: Interval
    ) -> list[Bar]:
        max_age = INTRADAY_MAX_AGE.get(interval)
        if max_age is not None and start < datetime.now(timezone.utc) - max_age:
            raise ProviderError(
                f"{interval} bars are only available for the last {max_age.days} days",
                self.name,
            )
        try:
            with _quiet_yfinance():
                df = yf.Ticker(yahoo_symbol(symbol)).history(
                    start=start, end=end, interval=interval, auto_adjust=False, actions=False
                )
        except YFRateLimitError as e:
            raise RateLimited(self.name) from e
        except Exception as e:  # yfinance raises many unrelated types
            raise ProviderError(f"{type(e).__name__}: {e}", self.name) from e

        if df.empty:
            # yfinance returns an empty frame both for unknown symbols and for
            # ranges with no trading; only the former is an error.
            if self._info(symbol).get("regularMarketPrice") is None:
                raise SymbolNotFound(str(symbol), self.name)
            return []

        bars = []
        for ts, row in df.iterrows():
            if any(_missing(row[c]) for c in ("Open", "High", "Low", "Close")):
                continue
            bars.append(
                Bar(
                    symbol=str(symbol),
                    timestamp=ts.to_pydatetime(),
                    interval=interval,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=None if _missing(row["Volume"]) else float(row["Volume"]),
                    source=self.name,
                )
            )
        return bars

    def _info(self, symbol: Symbol) -> dict[str, Any]:
        try:
            with _quiet_yfinance():
                info = yf.Ticker(yahoo_symbol(symbol)).info
        except YFRateLimitError as e:
            raise RateLimited(self.name) from e
        except Exception as e:  # yfinance raises many unrelated types
            raise ProviderError(f"{type(e).__name__}: {e}", self.name) from e
        return dict(info or {})


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))
