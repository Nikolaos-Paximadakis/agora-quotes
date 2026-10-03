from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from yfinance.exceptions import YFRateLimitError

from agora_quotes import ProviderError, RateLimited, SymbolNotFound
from agora_quotes.providers import yahoo
from agora_quotes.providers.yahoo import YahooProvider

INFO = {
    "EXAE.AT": {
        "regularMarketPrice": 10.22,
        "regularMarketTime": 1790950792,
        "currency": "EUR",
        "exchangeDataDelayedBy": 15,
    },
    "AAPL": {
        "regularMarketPrice": 333.69,
        "regularMarketTime": 1790971201,
        "currency": "USD",
        "exchangeDataDelayedBy": 0,
    },
}


@pytest.fixture
def fake_ticker(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    def ticker(symbol: str) -> Any:
        t = MagicMock()
        t.info = INFO.get(symbol, {"trailingPegRatio": None})
        return t

    mock = MagicMock(side_effect=ticker)
    monkeypatch.setattr(yahoo.yf, "Ticker", mock)
    return mock


def test_greek_quote_is_delayed(fake_ticker: MagicMock) -> None:
    q = YahooProvider().get_quote("EXAE.AT")
    assert q.price == 10.22
    assert q.currency == "EUR"
    assert q.delayed is True
    assert q.source == "yahoo"
    assert q.timestamp == datetime.fromtimestamp(1790950792, tz=timezone.utc)
    assert q.retrieved_at.tzinfo is timezone.utc


def test_us_quote_with_zero_delay_is_realtime(fake_ticker: MagicMock) -> None:
    assert YahooProvider().get_quote("AAPL").delayed is False


def test_missing_delay_field_counts_as_delayed(monkeypatch: pytest.MonkeyPatch) -> None:
    info = {k: v for k, v in INFO["AAPL"].items() if k != "exchangeDataDelayedBy"}
    monkeypatch.setattr(yahoo.yf, "Ticker", lambda s: MagicMock(info=info))
    assert YahooProvider().get_quote("AAPL").delayed is True


def test_unknown_symbol_raises_symbol_not_found(fake_ticker: MagicMock) -> None:
    with pytest.raises(SymbolNotFound) as exc:
        YahooProvider().get_quote("NOPE.AT")
    assert exc.value.symbol == "NOPE.AT"


def test_get_quotes_returns_errors_inline(fake_ticker: MagicMock) -> None:
    result = YahooProvider().get_quotes(["AAPL", "NOPE.AT", "EXAE.AT"])
    assert list(result) == ["AAPL", "NOPE.AT", "EXAE.AT"]
    assert isinstance(result["NOPE.AT"], SymbolNotFound)
    assert result["EXAE.AT"].price == 10.22  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("vendor_exc", "expected"),
    [(YFRateLimitError(), RateLimited), (ConnectionError("boom"), ProviderError)],
)
def test_vendor_exceptions_are_wrapped(
    monkeypatch: pytest.MonkeyPatch, vendor_exc: Exception, expected: type[Exception]
) -> None:
    class Exploding:
        @property
        def info(self) -> dict[str, Any]:
            raise vendor_exc

    monkeypatch.setattr(yahoo.yf, "Ticker", lambda s: Exploding())
    with pytest.raises(expected) as exc:
        YahooProvider().get_quote("AAPL")
    assert exc.value.__cause__ is vendor_exc


def test_get_quotes_raises_on_source_wide_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Exploding:
        @property
        def info(self) -> dict[str, Any]:
            raise YFRateLimitError()

    monkeypatch.setattr(yahoo.yf, "Ticker", lambda s: Exploding())
    with pytest.raises(RateLimited):
        YahooProvider().get_quotes(["AAPL", "EXAE.AT"])
