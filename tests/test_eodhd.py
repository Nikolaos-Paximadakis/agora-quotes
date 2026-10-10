import io
import json
from datetime import date, datetime, time, timedelta, timezone
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from agora_quotes import ConfigurationError, ProviderError, RateLimited, SymbolNotFound, registry
from agora_quotes.providers import eodhd
from agora_quotes.providers.eodhd import EODHDProvider, eodhd_symbol
from agora_quotes.symbols import Symbol

UTC = timezone.utc
EXAE = Symbol("EXAE", "ATHEX")
AAPL = Symbol("AAPL")
NOPE = Symbol("NOPE", "ATHEX")

# Shapes taken from real responses.
QUOTES = {
    "EXAE.AT": {
        "code": "EXAE.AT",
        "timestamp": 1790950792,
        "gmtoffset": 0,
        "open": 10.1,
        "high": 10.3,
        "low": 10.05,
        "close": 10.22,
        "volume": 81234,
        "previousClose": 10.1,
        "change": 0.12,
        "change_p": 1.1881,
    },
    "AAPL.US": {
        "code": "AAPL.US",
        "timestamp": 1790972880,
        "gmtoffset": 0,
        "open": 333.26,
        "high": 334.54,
        "low": 330.61,
        "close": 333.69,
        "volume": 31852902,
        "previousClose": 330.32,
        "change": 3.37,
        "change_p": 1.0202,
    },
}
NA_QUOTE = {
    "code": "NOPE.AT",
    "timestamp": "NA",
    "gmtoffset": 0,
    "open": "NA",
    "high": "NA",
    "low": "NA",
    "close": "NA",
    "volume": "NA",
}


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def http_error(code: int, body: str = "", headers: dict[str, str] | None = None) -> HTTPError:
    hdrs = Message()
    for k, v in (headers or {}).items():
        hdrs[k] = v
    return HTTPError("https://eodhd.com/api/x", code, "err", hdrs, io.BytesIO(body.encode()))


class FakeApi:
    """Stands in for ``urlopen``; records each call's path and query."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.responses: dict[str, Any] = {}  # by path; an Exception is raised
        self.forbidden: set[str] = set()  # tickers outside the plan

    def __call__(self, url: str, timeout: float) -> FakeResponse:
        parts = urlsplit(url)
        path = parts.path.removeprefix("/api/")
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.calls.append((path, query))
        if path in self.responses:
            response = self.responses[path]
            if isinstance(response, Exception):
                raise response
            return FakeResponse(json.dumps(response).encode())
        if path.startswith("real-time/"):
            tickers = [path.removeprefix("real-time/")]
            if "s" in query:
                tickers += query["s"].split(",")
            if self.forbidden & set(tickers):
                raise http_error(403, "Forbidden. Please contact support@eodhistoricaldata.com")
            rows = [QUOTES.get(t, {**NA_QUOTE, "code": t}) for t in tickers]
            return FakeResponse(json.dumps(rows[0] if len(rows) == 1 else rows).encode())
        raise http_error(404, "Ticker Not Found.")


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    monkeypatch.setenv("EODHD_API_KEY", "test-key")
    fake = FakeApi()
    monkeypatch.setattr(eodhd, "urlopen", fake)
    return fake


@pytest.fixture
def provider(api: FakeApi) -> EODHDProvider:
    return EODHDProvider()


def test_missing_api_key_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="EODHD_API_KEY"):
        registry.configure(provider="eodhd")


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        (Symbol("EXAE", "ATHEX"), "EXAE.AT"),
        (Symbol("AAPL"), "AAPL.US"),
        (Symbol("MSFT", "NASDAQ"), "MSFT.US"),
        (Symbol("VOD", "LSE"), "VOD.LSE"),
        (Symbol("SAP", "XETRA"), "SAP.XETRA"),
    ],
)
def test_eodhd_symbol(symbol: Symbol, expected: str) -> None:
    assert eodhd_symbol(symbol) == expected


# quotes ------------------------------------------------------------------


def test_greek_quote(provider: EODHDProvider, api: FakeApi) -> None:
    q = provider.get_quote(EXAE)
    assert q.symbol == "ATHEX:EXAE"
    assert q.price == 10.22
    assert q.currency == "EUR"
    assert q.timestamp == datetime.fromtimestamp(1790950792, tz=UTC)
    assert q.delayed is True
    assert q.source == "eodhd"
    path, query = api.calls[0]
    assert path == "real-time/EXAE.AT"
    assert query["api_token"] == "test-key" and query["fmt"] == "json"
    assert "s" not in query


def test_us_quote_is_still_delayed(provider: EODHDProvider) -> None:
    q = provider.get_quote(AAPL)
    assert (q.price, q.currency, q.delayed) == (333.69, "USD", True)


def test_unknown_symbol_na_response(provider: EODHDProvider) -> None:
    with pytest.raises(SymbolNotFound) as exc:
        provider.get_quote(NOPE)
    assert exc.value.symbol == "ATHEX:NOPE"


def test_unknown_symbol_404(provider: EODHDProvider, api: FakeApi) -> None:
    api.responses["real-time/NOPE.AT"] = http_error(404, "Ticker Not Found.")
    with pytest.raises(SymbolNotFound) as exc:
        provider.get_quote(NOPE)
    assert exc.value.symbol == "ATHEX:NOPE"
    assert isinstance(exc.value.__cause__, HTTPError)


def test_get_quotes_is_one_batched_request(provider: EODHDProvider, api: FakeApi) -> None:
    result = provider.get_quotes([AAPL, NOPE, EXAE])
    assert list(result) == [AAPL, NOPE, EXAE]
    assert result[AAPL].price == 333.69  # type: ignore[union-attr]
    assert isinstance(result[NOPE], SymbolNotFound)
    assert result[EXAE].price == 10.22  # type: ignore[union-attr]
    assert api.calls == [
        (
            "real-time/AAPL.US",
            {"s": "NOPE.AT,EXAE.AT", "api_token": "test-key", "fmt": "json"},
        )
    ]


def test_symbol_missing_from_batch_is_not_found(provider: EODHDProvider, api: FakeApi) -> None:
    api.responses["real-time/AAPL.US"] = [QUOTES["AAPL.US"]]
    result = provider.get_quotes([AAPL, EXAE])
    assert isinstance(result[EXAE], SymbolNotFound)


def test_forbidden_symbol_fails_inline(provider: EODHDProvider, api: FakeApi) -> None:
    # A ticker outside the plan makes EODHD refuse the whole batch.
    api.forbidden.add("EXAE.AT")
    result = provider.get_quotes([AAPL, EXAE])
    assert result[AAPL].price == 333.69  # type: ignore[union-attr]
    assert isinstance(result[EXAE], ProviderError)
    assert "ATHEX:EXAE" in str(result[EXAE])
    assert [p for p, _ in api.calls] == [
        "real-time/AAPL.US",
        "real-time/AAPL.US",
        "real-time/EXAE.AT",
    ]


def test_large_batches_are_split(provider: EODHDProvider, api: FakeApi) -> None:
    symbols = [Symbol(f"T{i}") for i in range(eodhd.BATCH_SIZE + 1)]
    result = provider.get_quotes(symbols)
    assert list(result) == symbols
    assert len(api.calls) == 2


def test_rate_limit_after_a_success_is_returned_inline(
    provider: EODHDProvider, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(eodhd, "BATCH_SIZE", 1)
    api.responses["real-time/EXAE.AT"] = http_error(429, "Too Many Requests")
    result = provider.get_quotes([AAPL, EXAE, NOPE])
    assert result[AAPL].price == 333.69  # type: ignore[union-attr]
    assert isinstance(result[EXAE], RateLimited)
    assert isinstance(result[NOPE], RateLimited)
    assert len(api.calls) == 2


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (http_error(429, "Too Many Requests", {"Retry-After": "30"}), RateLimited),
        (http_error(402, "Payment Required"), RateLimited),  # daily quota used up
        (http_error(401, "Unauthenticated"), ProviderError),
        (http_error(500, "oops"), ProviderError),
        (URLError("timed out"), ProviderError),
        (TimeoutError("timed out"), ProviderError),
    ],
)
def test_source_wide_errors_raise(
    provider: EODHDProvider, api: FakeApi, error: Exception, expected: type[Exception]
) -> None:
    api.responses["real-time/AAPL.US"] = error
    with pytest.raises(expected) as exc:
        provider.get_quotes([AAPL, EXAE])
    assert exc.value.__cause__ is error
    assert "test-key" not in str(exc.value)


def test_retry_after_is_kept(provider: EODHDProvider, api: FakeApi) -> None:
    api.responses["real-time/AAPL.US"] = http_error(429, "", {"Retry-After": "30"})
    with pytest.raises(RateLimited) as exc:
        provider.get_quote(AAPL)
    assert exc.value.retry_after == 30


def test_non_json_response_is_a_provider_error(
    provider: EODHDProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(eodhd, "urlopen", lambda url, timeout: FakeResponse(b"<html>"))
    with pytest.raises(ProviderError, match="non-JSON"):
        provider.get_quote(AAPL)


# history -----------------------------------------------------------------

EOD_ROWS = [
    {
        "date": "2026-09-01",
        "open": 9.8,
        "high": 10.0,
        "low": 9.7,
        "close": 9.9,
        "adjusted_close": 9.5,
        "volume": 120000,
    },
    {
        "date": "2026-09-02",
        "open": 9.9,
        "high": 10.2,
        "low": 9.85,
        "close": 10.1,
        "adjusted_close": 9.7,
        "volume": 98000,
    },
]


def test_daily_history(provider: EODHDProvider, api: FakeApi) -> None:
    api.responses["eod/EXAE.AT"] = EOD_ROWS
    bars = provider.get_history(
        EXAE, datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 3, tzinfo=UTC), "1d"
    )
    assert len(bars) == 2
    first = bars[0]
    # Midnight in Athens (UTC+3 in September), unadjusted close.
    assert first.timestamp == datetime(2026, 8, 31, 21, tzinfo=UTC)
    assert (first.open, first.high, first.low, first.close) == (9.8, 10.0, 9.7, 9.9)
    assert first.volume == 120000.0
    assert (first.symbol, first.interval, first.source) == ("ATHEX:EXAE", "1d", "eodhd")
    path, query = api.calls[0]
    assert path == "eod/EXAE.AT"
    # ``to`` is inclusive upstream; the requested end is exclusive.
    assert (query["from"], query["to"], query["period"]) == ("2026-09-01", "2026-09-02", "d")


def test_daily_history_drops_rows_outside_the_dates(provider: EODHDProvider, api: FakeApi) -> None:
    api.responses["eod/EXAE.AT"] = EOD_ROWS
    bars = provider.get_history(
        EXAE, datetime(2026, 9, 2, tzinfo=UTC), datetime(2026, 9, 3, tzinfo=UTC), "1d"
    )
    assert [b.close for b in bars] == [10.1]


def test_weekly_row_of_the_week_containing_start_is_kept(
    provider: EODHDProvider, api: FakeApi
) -> None:
    # 2026-09-01 is a Tuesday; EODHD dates the week by its first trading day.
    api.responses["eod/EXAE.AT"] = EOD_ROWS[:1]
    bars = provider.get_history(
        EXAE, datetime(2026, 9, 2, tzinfo=UTC), datetime(2026, 9, 4, tzinfo=UTC), "1wk"
    )
    assert [b.close for b in bars] == [9.9]
    assert api.calls[0][1]["from"] == "2026-08-31"


def test_history_older_than_the_plan_allows_raises(provider: EODHDProvider, api: FakeApi) -> None:
    # Measured on the free plan: a 2018 range returns the oldest bar it allows.
    api.responses["eod/EXAE.AT"] = EOD_ROWS
    with pytest.raises(ProviderError, match="limits how far back"):
        provider.get_history(
            EXAE, datetime(2018, 8, 27, tzinfo=UTC), datetime(2018, 8, 28, tzinfo=UTC), "1d"
        )


def eod_row(day: date, close: float = 1.0) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "adjusted_close": close,
        "volume": 1,
    }


def test_range_straddling_the_free_plans_year_raises(provider: EODHDProvider, api: FakeApi) -> None:
    # Measured on the free plan: rows start a year back, wherever the range starts.
    today = datetime.now(UTC).date()
    api.responses["eod/EXAE.AT"] = [eod_row(today - timedelta(days=d)) for d in (366, 365, 30)]
    with pytest.raises(ProviderError, match="limits how far back"):
        provider.get_history(
            EXAE,
            datetime.combine(today - timedelta(days=700), time(), UTC),
            datetime.combine(today, time(), UTC),
            "1d",
        )


def test_history_that_really_starts_late_is_returned(provider: EODHDProvider, api: FakeApi) -> None:
    # A listing that began mid-range, well away from the plan's one-year edge.
    today = datetime.now(UTC).date()
    api.responses["eod/EXAE.AT"] = [eod_row(today - timedelta(days=d)) for d in (100, 99)]
    bars = provider.get_history(
        EXAE,
        datetime.combine(today - timedelta(days=700), time(), UTC),
        datetime.combine(today, time(), UTC),
        "1d",
    )
    assert len(bars) == 2


@pytest.mark.parametrize(("interval", "period"), [("1wk", "w"), ("1mo", "m")])
def test_longer_periods(provider: EODHDProvider, api: FakeApi, interval: str, period: str) -> None:
    api.responses["eod/AAPL.US"] = []
    provider.get_history(
        AAPL,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 10, 1, tzinfo=UTC),
        interval,  # type: ignore[arg-type]
    )
    assert api.calls[0][1]["period"] == period


def test_intraday_history(provider: EODHDProvider, api: FakeApi) -> None:
    start = datetime(2026, 9, 21, 14, 30, tzinfo=UTC)
    end = start + timedelta(minutes=15)
    api.responses["intraday/AAPL.US"] = [
        {
            "timestamp": int((start + timedelta(minutes=m)).timestamp()),
            "gmtoffset": 0,
            "datetime": "",
            "open": 337.29,
            "high": 338.2,
            "low": 336.3,
            "close": 337.04,
            "volume": None if m else 3529230,
        }
        for m in (0, 5, 10, 15)
    ]
    bars = provider.get_history(AAPL, start, end, "5m")
    # The bar at ``end`` is dropped: ``to`` is inclusive upstream.
    assert [b.timestamp for b in bars] == [start + timedelta(minutes=m) for m in (0, 5, 10)]
    assert bars[0].volume == 3529230.0
    assert bars[1].volume is None
    query = api.calls[0][1]
    assert query["interval"] == "5m"
    assert (int(query["from"]), int(query["to"])) == (start.timestamp(), end.timestamp())


def test_intraday_range_too_long_is_rejected_up_front(
    provider: EODHDProvider, api: FakeApi
) -> None:
    end = datetime(2026, 10, 1, tzinfo=UTC)
    with pytest.raises(ProviderError, match="120 days"):
        provider.get_history(AAPL, end - timedelta(days=121), end, "1m")
    assert api.calls == []


def test_history_unknown_symbol(provider: EODHDProvider) -> None:
    with pytest.raises(SymbolNotFound) as exc:
        provider.get_history(
            NOPE, datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 3, tzinfo=UTC), "1d"
        )
    assert exc.value.symbol == "ATHEX:NOPE"


def test_history_with_no_trading_is_empty(provider: EODHDProvider, api: FakeApi) -> None:
    api.responses["eod/AAPL.US"] = []
    bars = provider.get_history(
        AAPL, datetime(2026, 9, 5, tzinfo=UTC), datetime(2026, 9, 7, tzinfo=UTC), "1d"
    )
    assert bars == []


def test_malformed_history_is_a_provider_error(provider: EODHDProvider, api: FakeApi) -> None:
    api.responses["eod/AAPL.US"] = [{"date": "2026-09-01", "open": "x"}]
    with pytest.raises(ProviderError, match="bad history"):
        provider.get_history(
            AAPL, datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 3, tzinfo=UTC), "1d"
        )
