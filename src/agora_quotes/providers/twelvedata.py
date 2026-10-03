"""Twelve Data adapter, built on the official ``twelvedata`` client.

Install with ``agora-quotes[twelvedata]`` and set ``TWELVEDATA_API_KEY``.
Symbols are sent as the ticker plus the exchange's ISO 10383 MIC code from
``MIC_CODES`` (``ATHEX:EXAE`` -> ``EXAE`` on ``XATH``); plain tickers go
without one, which Twelve Data treats as US.

Twelve Data's responses never say whether a price is real-time, so every
quote is marked delayed. Its websocket API is callback-based; ``stream`` will
bridge it into an async generator through an ``asyncio.Queue``.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from twelvedata.exceptions import TwelveDataError
from twelvedata.http_client import DefaultHttpClient

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

    def stream(self, symbols: Sequence[Symbol]) -> AsyncIterator[Quote]:
        raise NotImplementedError("Twelve Data streaming is not implemented yet")

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


class _NoData(ProviderError):
    """The symbol exists but has no bars in the requested range."""


class _NoAccess(ProviderError):
    """The API plan does not cover this symbol; a per-symbol failure."""
