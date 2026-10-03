"""Twelve Data adapter, built on the official ``twelvedata`` client.

Install with ``agora-quotes[twelvedata]`` and set ``TWELVEDATA_API_KEY``.
Symbols are sent as the ticker plus the exchange's ISO 10383 MIC code from
``MIC_CODES`` (``ATHEX:EXAE`` -> ``EXAE`` on ``XATH``); plain tickers go
without one, which Twelve Data treats as US.

Twelve Data's responses never say whether a price is real-time, so every
quote is marked delayed. Its websocket client is callback-based and runs on
its own threads; ``stream`` bridges it into an async generator through an
``asyncio.Queue``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import queue
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import websocket  # noqa: F401  # the SDK imports it lazily on its own thread; fail early instead
from twelvedata.context import Context
from twelvedata.exceptions import TwelveDataError
from twelvedata.http_client import DefaultHttpClient
from twelvedata.websocket import EventReceiver, TDWebSocket

from agora_quotes.errors import (
    AgoraQuotesError,
    ConfigurationError,
    ProviderError,
    RateLimited,
    SymbolNotFound,
)
from agora_quotes.models import Bar, Interval, Quote
from agora_quotes.symbols import Symbol

# Canonical exchange code -> ISO 10383 MIC code, as Twelve Data's mic_code.
MIC_CODES: dict[str, str] = {
    "ATHEX": "XATH",
    "NASDAQ": "XNAS",
    "NYSE": "XNYS",
    "LSE": "XLON",
    "XETRA": "XETR",
}
INTERVALS: dict[str, str] = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "1d": "1day",
    "1wk": "1week",
    "1mo": "1month",
}
INTRADAY = ("1m", "5m", "15m", "1h")
MAX_OUTPUTSIZE = 5000  # Twelve Data's per-request cap on bars
HEARTBEAT_S = 10.0  # Twelve Data asks clients to send a heartbeat this often
MAX_RECONNECTS = 5  # consecutive failed reconnects before the stream gives up
RECONNECT_DELAY_S = 1.0

log = logging.getLogger(__name__)


def twelvedata_params(symbol: Symbol) -> dict[str, str]:
    params = {"symbol": symbol.ticker}
    if symbol.exchange:
        params["mic_code"] = MIC_CODES[symbol.exchange]
    return params


class _HttpClient(DefaultHttpClient):  # type: ignore[misc]
    """Keeps the API error code on the exception; the stock client drops it."""

    @staticmethod
    def _raise_error(error_code: int, message: str) -> None:
        try:
            DefaultHttpClient._raise_error(error_code, message)
        except TwelveDataError as e:
            e.code = error_code
            raise


class TwelveDataProvider:
    name = "twelvedata"

    def __init__(self) -> None:
        key = os.environ.get("TWELVEDATA_API_KEY", "").strip()
        if not key:
            raise ConfigurationError("TWELVEDATA_API_KEY is not set")
        self._key = key
        # The client's HTTP layer, not TDClient: TDClient() makes a network
        # request for chart metadata as soon as it is constructed.
        self._http = _HttpClient("https://api.twelvedata.com")

    def get_quote(self, symbol: Symbol) -> Quote:
        try:
            data = self._get(symbol, "quote", twelvedata_params(symbol))
        except _NoData as e:
            raise SymbolNotFound(str(symbol), self.name) from e
        try:
            price = float(data["close"])
            market_time = datetime.fromtimestamp(
                int(data.get("last_quote_at") or data["timestamp"]), tz=timezone.utc
            )
        except (KeyError, ValueError, TypeError) as e:  # malformed response
            raise ProviderError(f"bad quote response for {symbol}: {e!r}", self.name) from e
        return Quote(
            symbol=str(symbol),
            price=price,
            currency=data.get("currency"),
            timestamp=market_time,
            # Twelve Data never reports whether a quote is real-time.
            delayed=True,
            source=self.name,
            retrieved_at=datetime.now(timezone.utc),
        )

    def get_quotes(self, symbols: Sequence[Symbol]) -> dict[Symbol, Quote | AgoraQuotesError]:
        # One request per symbol. If the rate limit hits partway through, the
        # quotes already paid for are returned and the rest fail inline, so
        # the service can cache them and retry only the rest on the fallback.
        results: dict[Symbol, Quote | AgoraQuotesError] = {}
        for i, symbol in enumerate(symbols):
            try:
                results[symbol] = self.get_quote(symbol)
            except (SymbolNotFound, _NoAccess) as e:
                results[symbol] = e
            except RateLimited as e:
                if not any(isinstance(v, Quote) for v in results.values()):
                    raise
                results.update(dict.fromkeys(symbols[i:], e))
                break
        return results

    def get_history(
        self, symbol: Symbol, start: datetime, end: datetime, interval: Interval
    ) -> list[Bar]:
        # Twelve Data's end_date is inclusive. Daily and longer bars are dated
        # in exchange time and ignore the timezone parameter, so those ranges
        # are sent as UTC dates: [start date, end date).
        if interval in INTRADAY:
            range_params = {
                "start_date": f"{start:%Y-%m-%d %H:%M:%S}",
                "end_date": f"{end:%Y-%m-%d %H:%M:%S}",
            }
        else:
            range_params = {
                "start_date": start.date().isoformat(),
                "end_date": (end - timedelta(microseconds=1)).date().isoformat(),
            }
        try:
            data = self._get(
                symbol,
                "time_series",
                {
                    **twelvedata_params(symbol),
                    **range_params,
                    "interval": INTERVALS[interval],
                    "timezone": "UTC",
                    "adjust": "none",
                    "order": "asc",
                    "outputsize": MAX_OUTPUTSIZE,
                },
            )
        except _NoData:
            return []
        try:
            return self._bars(symbol, data, interval, end)
        except (KeyError, ValueError, TypeError) as e:  # malformed response
            raise ProviderError(f"bad time_series response: {e!r}", self.name) from e

    def _bars(
        self, symbol: Symbol, data: dict[str, Any], interval: Interval, end: datetime
    ) -> list[Bar]:
        intraday = interval in INTRADAY
        values: list[dict[str, Any]] = data.get("values") or []
        if len(values) >= MAX_OUTPUTSIZE:
            raise ProviderError(
                f"more than {MAX_OUTPUTSIZE} bars in range; request a shorter range", self.name
            )
        exchange_tz = ZoneInfo(data.get("meta", {}).get("exchange_timezone") or "UTC")

        bars = []
        for row in values:
            if intraday:
                ts = datetime.fromisoformat(row["datetime"]).replace(tzinfo=timezone.utc)
                if ts >= end:
                    continue
            else:
                # Bar start: midnight of the trading date in exchange time.
                ts = datetime.combine(date.fromisoformat(row["datetime"]), time(), exchange_tz)
            volume = row.get("volume")
            bars.append(
                Bar(
                    symbol=str(symbol),
                    timestamp=ts,
                    interval=interval,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=None if volume in (None, "") else float(volume),
                    source=self.name,
                )
            )
        return bars

    async def stream(self, symbols: Sequence[Symbol]) -> AsyncIterator[Quote]:
        """Yield a quote for every price update on ``symbols`` until cancelled.

        Raises ``ProviderError`` if the connection is refused, cannot be
        re-established, or Twelve Data rejects any of the symbols.
        """
        if not symbols:
            return
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[dict[str, Any] | AgoraQuotesError] = asyncio.Queue()

        def put(item: dict[str, Any] | AgoraQuotesError) -> None:  # called on SDK threads
            loop.call_soon_threadsafe(events.put_nowait, item)

        sock = _Socket(self._key, put)
        by_key = {_ws_key(s): s for s in symbols}
        sock.subscribe(list(by_key))  # sent once the connection opens
        sock.connect()
        try:
            next_heartbeat = loop.time() + HEARTBEAT_S
            while True:
                timeout = next_heartbeat - loop.time()
                if timeout <= 0:
                    await asyncio.to_thread(sock.heartbeat)
                    next_heartbeat = loop.time() + HEARTBEAT_S
                    continue
                try:
                    item = await asyncio.wait_for(events.get(), timeout)
                except asyncio.TimeoutError:
                    continue
                if isinstance(item, AgoraQuotesError):
                    raise item
                for quote in self._stream_event(item, by_key):
                    yield quote
        finally:
            await asyncio.to_thread(sock.close)

    def _stream_event(self, event: dict[str, Any], by_key: dict[str, Symbol]) -> list[Quote]:
        kind = event.get("event")
        if kind == "price":
            # A plain ticker was subscribed without a MIC code, but its events carry one.
            ticker = event.get("symbol", "")
            matches = [
                by_key[k] for k in (f"{ticker}:{event.get('mic_code')}", ticker) if k in by_key
            ]
            try:
                price = float(event["price"])
                market_time = datetime.fromtimestamp(int(event["timestamp"]), tz=timezone.utc)
            except (KeyError, ValueError, TypeError) as e:
                raise ProviderError(f"bad price event {event!r}: {e!r}", self.name) from e
            now = datetime.now(timezone.utc)
            return [
                Quote(
                    symbol=str(symbol),
                    price=price,
                    currency=event.get("currency"),
                    timestamp=market_time,
                    delayed=True,  # as with REST, nothing says the feed is real-time
                    source=self.name,
                    retrieved_at=now,
                )
                for symbol in matches
            ]
        if kind == "subscribe-status":
            fails = event.get("fails") or []
            if fails or event.get("status") != "ok":
                rejected = [
                    str(by_key.get(f"{f.get('symbol')}:{f.get('mic_code')}", f.get("symbol")))
                    for f in fails
                ]
                raise ProviderError(f"subscription rejected for {rejected or event}", self.name)
            return []
        if event.get("status") == "error":
            raise ProviderError(f"websocket error: {event.get('message') or event}", self.name)
        return []  # heartbeat replies and anything new

    def _get(self, symbol: Symbol, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """Call ``endpoint`` and return the full JSON body, mapping API errors."""
        try:
            resp = self._http.get(f"/{endpoint}", params={**params, "apikey": self._key})
            raw = resp.text
            body = json.loads(raw)
        except TwelveDataError as e:
            raise self._error(symbol, e) from e
        except Exception as e:  # requests and JSON errors
            raise ProviderError(f"{type(e).__name__}: {e}", self.name) from e
        if not isinstance(body, dict):
            raise ProviderError(f"unexpected {endpoint} response: {raw[:200]}", self.name)
        return body

    def _error(self, symbol: Symbol, e: TwelveDataError) -> AgoraQuotesError:
        code = getattr(e, "code", None)
        message = str(e)
        if code == 429:
            return RateLimited(self.name)
        if code in (400, 404) and "no data is available" in message.lower():
            return _NoData(message, self.name)
        if code == 404 or (code == 400 and "symbol" in message.lower()):
            return SymbolNotFound(str(symbol), self.name)
        if code == 403:
            return _NoAccess(f"{symbol}: {message}", self.name)
        return ProviderError(f"{code}: {message}", self.name)


def _ws_key(symbol: Symbol) -> str:
    """The SDK tracks subscriptions as strings; ``_Socket`` sends them as objects."""
    if symbol.exchange:
        return f"{symbol.ticker}:{MIC_CODES[symbol.exchange]}"
    return symbol.ticker


class _Socket(TDWebSocket):  # type: ignore[misc]
    """The SDK's websocket client, with the gaps ``stream`` needs filled.

    * Symbols are sent as ``{"symbol", "mic_code"}`` objects; the stock client
      can only send a comma-joined string.
    * A connection that fails before it ever opened (bad key, no network), or
      fails ``MAX_RECONNECTS`` times in a row, is reported instead of retried
      forever with only a log line.
    * ``close`` also ends the SDK's dispatch thread, which otherwise lives on.
    """

    def __init__(
        self, apikey: str, put: Callable[[dict[str, Any] | AgoraQuotesError], None]
    ) -> None:
        self._put = put
        self._closed = False
        self._opened = False
        self._failures = 0
        self.last_error: BaseException | None = None
        ctx = Context()
        ctx.apikey = apikey
        ctx.self_heal_time_s = RECONNECT_DELAY_S
        # Our logger: the default one adds a stderr handler per instance.
        ctx.defaults = {"on_event": self._on_event, "logger": log}
        super().__init__(ctx)

    def _on_event(self, event: dict[str, Any]) -> None:
        if self._closed:
            # BaseException passes the dispatch loop's ``except Exception``
            # and ends the thread quietly.
            raise SystemExit
        self._put(event)

    @staticmethod
    def subscribe_event(symbols: set[str]) -> dict[str, Any]:
        return {"action": "subscribe", "params": {"symbols": _ws_objects(symbols)}}

    @staticmethod
    def unsubscribe_event(symbols: set[str]) -> dict[str, Any]:
        return {"action": "unsubscribe", "params": {"symbols": _ws_objects(symbols)}}

    def refresh_websocket(self) -> None:
        if self._closed:  # closed while a reconnect was sleeping
            return
        self.event_receiver = _Receiver(self)
        self.event_receiver.start()

    def on_connect(self) -> None:
        if self._closed:  # opened just as it was closed
            self.ws.close()
            return
        self._opened = True
        self._failures = 0
        super().on_connect()

    def self_heal(self) -> None:  # called on the receiver thread after an error
        if self._closed:
            return
        self._failures += 1
        if not self._opened or self._failures > MAX_RECONNECTS:
            what = "reconnect" if self._opened else "connect"
            error = ProviderError(f"websocket could not {what}: {self.last_error}", "twelvedata")
            error.__cause__ = self.last_error
            self._put(error)
            return
        super().self_heal()

    def close(self) -> None:
        self._closed = True
        self.ready = False
        if self.ws:
            self.ws.close()
        # Wake the dispatch thread so it can exit; if the queue is full, it
        # exits on the next event anyway.
        with contextlib.suppress(queue.Full):
            self.events.put_nowait({})


class _Receiver(EventReceiver):  # type: ignore[misc]
    """Keeps the connection error, which the SDK only logs."""

    def on_error(self, ws: Any, error: BaseException) -> None:
        self.client.last_error = error
        super().on_error(ws, error)


def _ws_objects(symbols: set[str]) -> list[dict[str, str]]:
    objects = []
    for key in sorted(symbols):
        ticker, _, mic = key.partition(":")
        objects.append({"symbol": ticker, "mic_code": mic} if mic else {"symbol": ticker})
    return objects


class _NoData(ProviderError):
    """The symbol exists but has no bars in the requested range."""


class _NoAccess(ProviderError):
    """The API plan does not cover this symbol; a per-symbol failure."""
