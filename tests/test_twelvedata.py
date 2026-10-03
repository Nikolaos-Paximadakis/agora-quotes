import json
from datetime import datetime, timezone
from typing import Any

import pytest
from twelvedata.exceptions import TwelveDataError

from agora_quotes import ConfigurationError, ProviderError, RateLimited, SymbolNotFound, registry
from agora_quotes.providers.twelvedata import TwelveDataProvider
from agora_quotes.symbols import Symbol

UTC = timezone.utc
EXAE = Symbol("EXAE", "ATHEX")
AAPL = Symbol("AAPL")
NOPE = Symbol("NOPE", "ATHEX")

QUOTES = {
    "EXAE": {
        "symbol": "EXAE",
        "exchange": "ATHEX",
        "mic_code": "XATH",
        "currency": "EUR",
        "datetime": "2026-10-02",
        "timestamp": 1790888400,
        "last_quote_at": 1790950792,
        "close": "10.22000",
        "is_market_open": False,
    },
    "AAPL": {
        "symbol": "AAPL",
        "currency": "USD",
        "timestamp": 1790971201,
        "close": "333.69000",
    },
}
NOT_FOUND = {
    "code": 400,
    "message": "**symbol** not found: NOPE. Please specify it correctly.",
    "status": "error",
}


class FakeResponse:
    def __init__(self, body: Any, status: int = 200) -> None:
        self.status_code = status
        self.ok = status < 400
        self.headers = {"Content-Type": "application/json"}
        self.text = json.dumps(body)

    def json(self) -> Any:
        return json.loads(self.text)


class FakeApi:
    """Stands in for the HTTP session; records each call's endpoint and params."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, FakeResponse] = {}  # by symbol
        self.error: Exception | None = None

    def get(self, url: str, params: dict[str, Any], **kwargs: Any) -> FakeResponse:
        endpoint = url.rsplit("/", 1)[1]
        self.calls.append((endpoint, dict(params)))
        if self.error is not None:
            raise self.error
        symbol = params["symbol"]
        if symbol in self.responses:
            return self.responses[symbol]
        if endpoint == "quote" and symbol in QUOTES:
            return FakeResponse(QUOTES[symbol])
        return FakeResponse(NOT_FOUND)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    monkeypatch.setenv("TWELVEDATA_API_KEY", "test-key")
    return FakeApi()


@pytest.fixture
def provider(api: FakeApi) -> TwelveDataProvider:
    p = TwelveDataProvider()
    p._http.session = api
    return p


def test_missing_api_key_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="TWELVEDATA_API_KEY"):
        registry.configure(provider="twelvedata")


# quotes ------------------------------------------------------------------


def test_greek_quote(provider: TwelveDataProvider, api: FakeApi) -> None:
    q = provider.get_quote(EXAE)
    assert q.symbol == "ATHEX:EXAE"
    assert q.price == 10.22
    assert q.currency == "EUR"
    assert q.timestamp == datetime.fromtimestamp(1790950792, tz=UTC)  # last_quote_at
    assert q.delayed is True
    assert q.source == "twelvedata"
    endpoint, params = api.calls[0]
    assert endpoint == "quote"
    assert params["symbol"] == "EXAE" and params["mic_code"] == "XATH"
    assert params["apikey"] == "test-key"


def test_us_quote_is_still_delayed_and_has_no_mic_code(
    provider: TwelveDataProvider, api: FakeApi
) -> None:
    q = provider.get_quote(AAPL)
    assert q.delayed is True  # Twelve Data never says a quote is real-time
    assert q.timestamp == datetime.fromtimestamp(1790971201, tz=UTC)  # no last_quote_at
    assert "mic_code" not in api.calls[0][1]


def test_unknown_symbol_raises_symbol_not_found(provider: TwelveDataProvider) -> None:
    with pytest.raises(SymbolNotFound) as exc:
        provider.get_quote(NOPE)
    assert exc.value.symbol == "ATHEX:NOPE"
    assert isinstance(exc.value.__cause__, TwelveDataError)


def test_get_quotes_returns_errors_inline(provider: TwelveDataProvider, api: FakeApi) -> None:
    api.responses["AAPL"] = FakeResponse(
        {"code": 403, "message": "available starting with Grow plan", "status": "error"}
    )
    result = provider.get_quotes([AAPL, NOPE, EXAE])
    assert list(result) == [AAPL, NOPE, EXAE]
    assert isinstance(result[AAPL], ProviderError)  # not covered by the plan
    assert isinstance(result[NOPE], SymbolNotFound)
    assert result[EXAE].price == 10.22  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (FakeResponse({"code": 429, "message": "out of API credits"}, status=429), RateLimited),
        (
            FakeResponse({"code": 401, "message": "invalid api key", "status": "error"}),
            ProviderError,
        ),
        (
            FakeResponse({"code": 500, "message": "oops", "status": "error"}, status=500),
            ProviderError,
        ),
    ],
)
def test_api_errors_are_wrapped(
    provider: TwelveDataProvider, api: FakeApi, response: FakeResponse, expected: type[Exception]
) -> None:
    api.responses["AAPL"] = api.responses["EXAE"] = response
    with pytest.raises(expected) as exc:
        provider.get_quote(AAPL)
    assert isinstance(exc.value.__cause__, TwelveDataError)
    with pytest.raises(expected):
        provider.get_quotes([AAPL, EXAE])  # source-wide failures raise


def test_network_errors_are_wrapped(provider: TwelveDataProvider, api: FakeApi) -> None:
    api.error = ConnectionError("boom")
    with pytest.raises(ProviderError) as exc:
        provider.get_quote(AAPL)
    assert exc.value.__cause__ is api.error


# history -----------------------------------------------------------------

START = datetime(2026, 9, 28, tzinfo=UTC)
END = datetime(2026, 10, 3, tzinfo=UTC)


def series(tz: str, rows: list[tuple[str, str, str | None]]) -> FakeResponse:
    return FakeResponse(
        {
            "meta": {"symbol": "EXAE", "interval": "1day", "exchange_timezone": tz},
            "values": [
                {"datetime": ts, "open": c, "high": c, "low": c, "close": c, "volume": v}
                for ts, c, v in rows
            ],
            "status": "ok",
        }
    )


def test_daily_history_bars_start_at_exchange_midnight(
    provider: TwelveDataProvider, api: FakeApi
) -> None:
    api.responses["EXAE"] = series(
        "Europe/Athens", [("2026-09-28", "11.3", "99909"), ("2026-09-30", "10.9", None)]
    )
    bars = provider.get_history(EXAE, START, END, "1d")
    assert [b.timestamp for b in bars] == [
        datetime(2026, 9, 27, 21, 0, tzinfo=UTC),
        datetime(2026, 9, 29, 21, 0, tzinfo=UTC),
    ]
    assert bars[0].symbol == "ATHEX:EXAE"
    assert bars[0].close == 11.3 and bars[0].volume == 99909
    assert bars[1].volume is None
    assert all(b.source == "twelvedata" and b.interval == "1d" for b in bars)
    endpoint, params = api.calls[0]
    assert endpoint == "time_series"
    assert params["interval"] == "1day"
    assert params["adjust"] == "none"  # unadjusted
    assert params["start_date"] == "2026-09-28"
    assert params["end_date"] == "2026-10-02"  # inclusive upstream, so the day before END
    assert params["mic_code"] == "XATH"


def test_intraday_history_is_utc_and_excludes_end(
    provider: TwelveDataProvider, api: FakeApi
) -> None:
    start, end = (
        datetime(2026, 10, 2, 13, 30, tzinfo=UTC),
        datetime(2026, 10, 2, 13, 35, tzinfo=UTC),
    )
    api.responses["AAPL"] = series(
        "America/New_York",
        [("2026-10-02 13:30:00", "333.1", "1200"), ("2026-10-02 13:35:00", "333.2", "900")],
    )
    bars = provider.get_history(AAPL, start, end, "5m")
    assert [b.timestamp for b in bars] == [start]
    params = api.calls[0][1]
    assert params["interval"] == "5min" and params["timezone"] == "UTC"
    assert params["start_date"] == "2026-10-02 13:30:00"
    assert params["end_date"] == "2026-10-02 13:35:00"


def test_history_with_no_data_in_range_is_empty_list(
    provider: TwelveDataProvider, api: FakeApi
) -> None:
    api.responses["AAPL"] = FakeResponse(
        {
            "code": 400,
            "message": "No data is available on the specified dates.",
            "status": "error",
        }
    )
    assert provider.get_history(AAPL, START, END, "1d") == []


def test_history_for_unknown_symbol_raises(provider: TwelveDataProvider) -> None:
    with pytest.raises(SymbolNotFound):
        provider.get_history(NOPE, START, END, "1d")


def test_history_at_the_output_cap_raises(provider: TwelveDataProvider, api: FakeApi) -> None:
    api.responses["AAPL"] = series("America/New_York", [("2026-09-28", "1", "1")] * 5000)
    with pytest.raises(ProviderError, match="shorter range"):
        provider.get_history(AAPL, START, END, "1d")


def test_malformed_responses_raise_provider_error(
    provider: TwelveDataProvider, api: FakeApi
) -> None:
    api.responses["AAPL"] = FakeResponse({"symbol": "AAPL", "close": "n/a"})
    with pytest.raises(ProviderError, match="bad quote"):
        provider.get_quote(AAPL)
    api.responses["AAPL"] = FakeResponse({"meta": {}, "values": [{"datetime": "2026-09-28"}]})
    with pytest.raises(ProviderError, match="bad time_series"):
        provider.get_history(AAPL, START, END, "1d")
