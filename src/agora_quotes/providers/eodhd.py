"""EODHD adapter over the EODHD REST API, using only the standard library.

Set ``EODHD_API_KEY``. EODHD symbols are ``TICKER.EXCHANGE`` with its own
exchange codes from ``EXCHANGE_CODES`` (``ATHEX:EXAE`` -> ``EXAE.AT``); plain
tickers mean ``.US``.

Quotes come from the live endpoint, which EODHD documents as delayed (15-20
minutes for stocks) and never marks as real-time, so every quote is delayed.
It does not report a currency either; ``CURRENCIES`` fills it in per exchange
where it is unambiguous. History uses ``/eod`` (``close`` is unadjusted) for
daily and longer bars and ``/intraday`` for the rest.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from agora_quotes.errors import (
    AgoraQuotesError,
    ConfigurationError,
    ProviderError,
    RateLimited,
    SymbolNotFound,
)
from agora_quotes.models import Bar, Interval, Quote
from agora_quotes.symbols import Symbol

BASE_URL = "https://eodhd.com/api"
TIMEOUT_S = 15.0
# Canonical exchange code -> EODHD exchange code.
EXCHANGE_CODES: dict[str, str] = {
    "ATHEX": "AT",
    "NASDAQ": "US",
    "NYSE": "US",
    "LSE": "LSE",
    "XETRA": "XETRA",
}
TIMEZONES: dict[str, str] = {
    "AT": "Europe/Athens",
    "US": "America/New_York",
    "LSE": "Europe/London",
    "XETRA": "Europe/Berlin",
}
# LSE is left out: its listings are quoted in GBX, GBP or USD.
CURRENCIES: dict[str, str] = {"AT": "EUR", "US": "USD", "XETRA": "EUR"}
PERIODS: dict[str, str] = {"1d": "d", "1wk": "w", "1mo": "m"}
# Longest range EODHD serves in one intraday request.
INTRADAY_MAX_SPAN: dict[str, timedelta] = {
    "1m": timedelta(days=120),
    "5m": timedelta(days=600),
    "15m": timedelta(days=600),
    "1h": timedelta(days=7200),
}
BATCH_SIZE = 15  # EODHD suggests at most 15-20 tickers per live request


def eodhd_symbol(symbol: Symbol) -> str:
    return f"{symbol.ticker}.{_exchange(symbol)}"


def _exchange(symbol: Symbol) -> str:
    return EXCHANGE_CODES[symbol.exchange] if symbol.exchange else "US"


class _NoAccess(ProviderError):
    """The plan does not cover this symbol or endpoint (HTTP 403); a per-symbol failure."""


class EODHDProvider:
    name = "eodhd"

    def __init__(self) -> None:
        key = os.environ.get("EODHD_API_KEY", "").strip()
        if not key:
            raise ConfigurationError("EODHD_API_KEY is not set")
        self._key = key

    def get_quote(self, symbol: Symbol) -> Quote:
        result = self._fetch_quotes([symbol])[symbol]
        if isinstance(result, AgoraQuotesError):
            raise result
        return result

    def get_quotes(self, symbols: Sequence[Symbol]) -> dict[Symbol, Quote | AgoraQuotesError]:
        # One request per BATCH_SIZE symbols. If the rate limit hits partway
        # through, the quotes already paid for are returned and the rest fail
        # inline, so the service can cache them and retry only the rest.
        results: dict[Symbol, Quote | AgoraQuotesError] = {}
        unique = list(dict.fromkeys(symbols))
        for i in range(0, len(unique), BATCH_SIZE):
            chunk = unique[i : i + BATCH_SIZE]
            try:
                results.update(self._fetch_quotes(chunk))
            except RateLimited as e:
                if not any(isinstance(v, Quote) for v in results.values()):
                    raise
                results.update(dict.fromkeys(unique[i:], e))
                break
        return results

    def _fetch_quotes(self, symbols: list[Symbol]) -> dict[Symbol, Quote | AgoraQuotesError]:
        first, *rest = symbols
        params = {"s": ",".join(eodhd_symbol(s) for s in rest)} if rest else {}
        try:
            body = self._get(f"real-time/{quote(eodhd_symbol(first))}", params, first)
        except (SymbolNotFound, _NoAccess):
            if not rest:
                raise
            # One bad ticker fails the whole batch; ask for each on its own.
            results: dict[Symbol, Quote | AgoraQuotesError] = {}
            for s in symbols:
                try:
                    results.update(self._fetch_quotes([s]))
                except (SymbolNotFound, _NoAccess) as err:
                    results[s] = err
            return results
        rows = body if isinstance(body, list) else [body]
        by_code = {str(row.get("code", "")).upper(): row for row in rows if isinstance(row, dict)}
        now = datetime.now(timezone.utc)
        results = {}
        for s in symbols:
            row = by_code.get(eodhd_symbol(s))
            results[s] = self._quote(s, row, now) if row else SymbolNotFound(str(s), self.name)
        return results

    def _quote(
        self, symbol: Symbol, row: dict[str, Any], now: datetime
    ) -> Quote | AgoraQuotesError:
        # Unknown tickers come back with every field set to "NA".
        if row.get("close") in (None, "NA") or row.get("timestamp") in (None, "NA"):
            return SymbolNotFound(str(symbol), self.name)
        try:
            price = float(row["close"])
            market_time = datetime.fromtimestamp(int(row["timestamp"]), tz=timezone.utc)
        except (KeyError, ValueError, TypeError) as e:  # malformed response
            raise ProviderError(f"bad quote response for {symbol}: {e!r}", self.name) from e
        return Quote(
            symbol=str(symbol),
            price=price,
            currency=CURRENCIES.get(_exchange(symbol)),
            timestamp=market_time,
            # The live endpoint is documented as delayed and never says otherwise.
            delayed=True,
            source=self.name,
            retrieved_at=now,
        )

    def get_history(
        self, symbol: Symbol, start: datetime, end: datetime, interval: Interval
    ) -> list[Bar]:
        path_symbol = quote(eodhd_symbol(symbol))
        intraday = interval in INTRADAY_MAX_SPAN
        if intraday:
            if end - start > INTRADAY_MAX_SPAN[interval]:
                raise ProviderError(
                    f"{interval} history is limited to {INTRADAY_MAX_SPAN[interval].days} days "
                    "per request; request a shorter range",
                    self.name,
                )
            # Both bounds are inclusive unix times; bars at ``end`` are dropped below.
            path = f"intraday/{path_symbol}"
            params = {
                "interval": interval,
                "from": str(int(start.timestamp())),
                "to": str(int(end.timestamp())),
            }
        else:
            # ``to`` is an inclusive date, so the range is [start date, end date).
            path = f"eod/{path_symbol}"
            params = {
                "period": PERIODS[interval],
                "from": start.date().isoformat(),
                "to": (end - timedelta(microseconds=1)).date().isoformat(),
                "order": "a",
            }
        body = self._get(path, params, symbol)
        if not isinstance(body, list):
            raise ProviderError(f"unexpected {path.split('/')[0]} response: {body!r}", self.name)
        try:
            return self._bars(symbol, body, interval, end)
        except (KeyError, ValueError, TypeError) as e:  # malformed response
            raise ProviderError(f"bad history response: {e!r}", self.name) from e

    def _bars(
        self, symbol: Symbol, rows: list[dict[str, Any]], interval: Interval, end: datetime
    ) -> list[Bar]:
        exchange_tz = ZoneInfo(TIMEZONES[_exchange(symbol)])
        bars = []
        for row in rows:
            if interval in INTRADAY_MAX_SPAN:
                ts = datetime.fromtimestamp(int(row["timestamp"]), tz=timezone.utc)
                if ts >= end:
                    continue
            else:
                # Bar start: midnight of its first trading date in exchange time.
                ts = datetime.combine(date.fromisoformat(row["date"]), time(), exchange_tz)
            volume = row.get("volume")
            bars.append(
                Bar(
                    symbol=str(symbol),
                    timestamp=ts,
                    interval=interval,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),  # unadjusted; we ignore adjusted_close
                    volume=None if volume is None else float(volume),
                    source=self.name,
                )
            )
        return bars

    def _get(self, path: str, params: dict[str, str], symbol: Symbol) -> Any:
        """GET ``path`` (whose ticker is ``symbol``) and return the parsed JSON body."""
        query = urlencode({**params, "api_token": self._key, "fmt": "json"})
        try:
            with urlopen(f"{BASE_URL}/{path}?{query}", timeout=TIMEOUT_S) as resp:
                raw = resp.read()
        except HTTPError as e:
            raise self._error(symbol, e) from e
        except (URLError, OSError) as e:  # network errors and timeouts
            raise ProviderError(f"{type(e).__name__}: {e}", self.name) from e
        try:
            return json.loads(raw)
        except ValueError as e:
            raise ProviderError(f"non-JSON response: {raw[:200]!r}", self.name) from e

    def _error(self, symbol: Symbol, e: HTTPError) -> AgoraQuotesError:
        try:
            message = e.read().decode("utf-8", "replace").strip()[:200]
        except Exception:
            message = ""
        message = message or str(e.reason)
        if e.code in (402, 429):  # 402: daily API call quota used up
            retry_after = e.headers.get("Retry-After") if e.headers else None
            try:
                return RateLimited(self.name, float(retry_after) if retry_after else None)
            except ValueError:
                return RateLimited(self.name)
        if e.code == 404:  # "Ticker Not Found."
            return SymbolNotFound(str(symbol), self.name)
        if e.code == 403:
            return _NoAccess(f"{symbol}: {message}", self.name)
        return ProviderError(f"{e.code}: {message}", self.name)
