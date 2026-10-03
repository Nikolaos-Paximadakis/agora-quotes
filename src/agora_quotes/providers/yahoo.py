"""Yahoo Finance adapter, built on yfinance.

Yahoo symbols are the ticker plus an exchange suffix from
``symbols.YAHOO_SUFFIXES`` (``ATHEX:EXAE`` -> ``EXAE.AT``; US tickers have no
suffix). Yahoo data is for personal use only; see the README for
redistribution terms.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import AsyncGenerator, Iterator, Sequence
from contextlib import contextmanager, suppress
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

MAX_RECONNECTS = 5  # consecutive failed reconnects before the stream gives up
RECONNECT_DELAY_S = 3.0
MARKET_HOURS_REGULAR = 1  # PricingData.market_hours: 0 pre, 1 regular, 2 post, 3 extended


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

    async def stream(self, symbols: Sequence[Symbol]) -> AsyncGenerator[Quote, None]:
        """Yield a quote for every regular-session price update until cancelled.

        Yahoo's websocket silently ignores unknown tickers and never says how
        delayed it is, so each symbol's ``info`` is fetched first: an unknown
        symbol raises ``SymbolNotFound``, and ``delayed`` comes from
        ``exchangeDataDelayedBy`` as in ``get_quote``. Raises ``ProviderError``
        if the first connection fails or a dropped one can't be restored.
        """
        if not symbols:
            return
        unique = list(dict.fromkeys(symbols))
        by_ticker: dict[str, list[Symbol]] = {}
        for s in unique:
            by_ticker.setdefault(yahoo_symbol(s), []).append(s)
        infos = await asyncio.gather(*(asyncio.to_thread(self._info, s) for s in unique))
        meta: dict[Symbol, tuple[bool, str | None]] = {}  # delayed, fallback currency
        for s, info in zip(unique, infos, strict=True):
            if info.get("regularMarketPrice") is None:
                raise SymbolNotFound(str(s), self.name)
            meta[s] = (info.get("exchangeDataDelayedBy") != 0, info.get("currency"))

        failures = 0  # consecutive reconnects that never delivered a message
        connected_once = False
        while True:
            sock = yf.AsyncWebSocket(verbose=False)
            try:
                try:
                    with _quiet_yfinance():
                        await sock.subscribe(list(by_ticker))  # connects first
                except Exception as e:
                    if not connected_once:
                        raise ProviderError(f"cannot connect: {e!r}", self.name) from e
                else:
                    connected_once = True
                    messages = aiter(sock._ws)
                    while True:
                        try:
                            raw = await anext(messages)
                        except Exception:  # clean close (StopAsyncIteration) or a drop
                            break
                        failures = 0
                        for quote in self._stream_message(sock, raw, by_ticker, meta):
                            yield quote
            finally:
                await _close(sock)
            failures += 1
            if failures > MAX_RECONNECTS:
                raise ProviderError(
                    f"websocket lost; {MAX_RECONNECTS} reconnect attempts failed", self.name
                )
            await asyncio.sleep(RECONNECT_DELAY_S)

    def _stream_message(
        self,
        sock: Any,
        raw: str | bytes,
        by_ticker: dict[str, list[Symbol]],
        meta: dict[Symbol, tuple[bool, str | None]],
    ) -> list[Quote]:
        try:
            with _quiet_yfinance():
                data = sock._decode_message(json.loads(raw).get("message", ""))
            matches = by_ticker.get(data.get("id", ""), [])
            # Pre/post-market updates are skipped, as get_quote reports the
            # regular-session price. proto3 omits 0 (PRE_MARKET), so absent
            # also means not regular.
            if not matches or data.get("market_hours") != MARKET_HOURS_REGULAR:
                return []
            price = float(data["price"])
            market_time = datetime.fromtimestamp(int(data["time"]) / 1000, tz=timezone.utc)
        except Exception:  # undecodable or incomplete; Yahoo sends no error events
            return []
        now = datetime.now(timezone.utc)
        return [
            Quote(
                symbol=str(s),
                price=price,
                currency=data.get("currency") or meta[s][1],
                timestamp=market_time,
                delayed=meta[s][0],
                source=self.name,
                retrieved_at=now,
            )
            for s in matches
        ]

    def _info(self, symbol: Symbol) -> dict[str, Any]:
        try:
            with _quiet_yfinance():
                info = yf.Ticker(yahoo_symbol(symbol)).info
        except YFRateLimitError as e:
            raise RateLimited(self.name) from e
        except Exception as e:  # yfinance raises many unrelated types
            raise ProviderError(f"{type(e).__name__}: {e}", self.name) from e
        return dict(info or {})


async def _close(sock: Any) -> None:
    with suppress(Exception):  # already gone; nothing to clean up
        await sock.close()


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))
