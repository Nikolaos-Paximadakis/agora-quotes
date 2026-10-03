import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest
from yfinance.exceptions import YFRateLimitError

from agora_quotes import ProviderError, RateLimited, SymbolNotFound
from agora_quotes.providers import yahoo
from agora_quotes.providers.yahoo import YahooProvider
from agora_quotes.symbols import Symbol

UTC = timezone.utc
EXAE = Symbol("EXAE", "ATHEX")
AAPL = Symbol("AAPL")
NOPE = Symbol("NOPE", "ATHEX")

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


def history_frame(rows: list[tuple[str, float, float | None]]) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp(ts) for ts, _, _ in rows], name="Date")
    return pd.DataFrame(
        {
            "Open": [c for _, c, _ in rows],
            "High": [c for _, c, _ in rows],
            "Low": [c for _, c, _ in rows],
            "Close": [c for _, c, _ in rows],
            "Adj Close": [c for _, c, _ in rows],
            "Volume": [v for _, _, v in rows],
        },
        index=index,
    )


@pytest.fixture
def tickers(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Fake yf.Ticker; tests may set ``.history.return_value`` per Yahoo symbol."""
    made: dict[str, MagicMock] = {}

    def ticker(symbol: str) -> Any:
        if symbol not in made:
            t = MagicMock()
            t.info = INFO.get(symbol, {"trailingPegRatio": None})
            t.history.return_value = history_frame([])
            made[symbol] = t
        return made[symbol]

    monkeypatch.setattr(yahoo.yf, "Ticker", ticker)
    return made


def exploding(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    class Exploding:
        @property
        def info(self) -> dict[str, Any]:
            raise exc

        def history(self, **kwargs: Any) -> pd.DataFrame:
            raise exc

    monkeypatch.setattr(yahoo.yf, "Ticker", lambda s: Exploding())


# quotes ------------------------------------------------------------------


def test_greek_quote_is_delayed(tickers: dict[str, MagicMock]) -> None:
    q = YahooProvider().get_quote(EXAE)
    assert q.symbol == "ATHEX:EXAE"
    assert q.price == 10.22
    assert q.currency == "EUR"
    assert q.delayed is True
    assert q.source == "yahoo"
    assert q.timestamp == datetime.fromtimestamp(1790950792, tz=UTC)
    assert q.retrieved_at.tzinfo is UTC
    assert "EXAE.AT" in tickers  # mapped to Yahoo's format


def test_us_quote_with_zero_delay_is_realtime(tickers: dict[str, MagicMock]) -> None:
    assert YahooProvider().get_quote(AAPL).delayed is False


def test_missing_delay_field_counts_as_delayed(monkeypatch: pytest.MonkeyPatch) -> None:
    info = {k: v for k, v in INFO["AAPL"].items() if k != "exchangeDataDelayedBy"}
    monkeypatch.setattr(yahoo.yf, "Ticker", lambda s: MagicMock(info=info))
    assert YahooProvider().get_quote(AAPL).delayed is True


def test_unknown_symbol_raises_symbol_not_found(tickers: dict[str, MagicMock]) -> None:
    with pytest.raises(SymbolNotFound) as exc:
        YahooProvider().get_quote(NOPE)
    assert exc.value.symbol == "ATHEX:NOPE"


def test_get_quotes_returns_errors_inline(tickers: dict[str, MagicMock]) -> None:
    result = YahooProvider().get_quotes([AAPL, NOPE, EXAE])
    assert list(result) == [AAPL, NOPE, EXAE]
    assert isinstance(result[NOPE], SymbolNotFound)
    assert result[EXAE].price == 10.22  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("vendor_exc", "expected"),
    [(YFRateLimitError(), RateLimited), (ConnectionError("boom"), ProviderError)],
)
def test_vendor_exceptions_are_wrapped(
    monkeypatch: pytest.MonkeyPatch, vendor_exc: Exception, expected: type[Exception]
) -> None:
    exploding(monkeypatch, vendor_exc)
    with pytest.raises(expected) as exc:
        YahooProvider().get_quote(AAPL)
    assert exc.value.__cause__ is vendor_exc
    with pytest.raises(expected):
        YahooProvider().get_history(
            AAPL, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC), "1d"
        )


def test_get_quotes_raises_on_source_wide_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    exploding(monkeypatch, YFRateLimitError())
    with pytest.raises(RateLimited):
        YahooProvider().get_quotes([AAPL, EXAE])


# history -----------------------------------------------------------------

START = datetime(2026, 9, 28, tzinfo=UTC)
END = datetime(2026, 10, 3, tzinfo=UTC)


def test_history_converts_rows_to_utc_bars(tickers: dict[str, MagicMock]) -> None:
    yahoo.yf.Ticker("EXAE.AT").history.return_value = history_frame(
        [
            ("2026-09-28 00:00:00+03:00", 11.3, 99909),
            ("2026-09-29 00:00:00+03:00", float("nan"), 0),  # incomplete row: dropped
            ("2026-09-30 00:00:00+03:00", 10.9, float("nan")),  # unknown volume
        ]
    )
    bars = YahooProvider().get_history(EXAE, START, END, "1d")
    assert [b.timestamp for b in bars] == [
        datetime(2026, 9, 27, 21, 0, tzinfo=UTC),
        datetime(2026, 9, 29, 21, 0, tzinfo=UTC),
    ]
    assert bars[0].symbol == "ATHEX:EXAE"
    assert bars[0].close == 11.3 and bars[0].volume == 99909
    assert bars[1].volume is None
    assert all(b.source == "yahoo" and b.interval == "1d" for b in bars)
    tickers["EXAE.AT"].history.assert_called_once_with(
        start=START, end=END, interval="1d", auto_adjust=False, actions=False
    )


def test_empty_history_for_existing_symbol_is_empty_list(tickers: dict[str, MagicMock]) -> None:
    assert YahooProvider().get_history(AAPL, START, END, "1d") == []


def test_empty_history_for_unknown_symbol_raises(tickers: dict[str, MagicMock]) -> None:
    with pytest.raises(SymbolNotFound):
        YahooProvider().get_history(NOPE, START, END, "1d")


def test_intraday_range_too_old_is_rejected_without_calling_yahoo(
    tickers: dict[str, MagicMock],
) -> None:
    old = datetime.now(UTC) - timedelta(days=31)
    with pytest.raises(ProviderError, match="last 30 days"):
        YahooProvider().get_history(AAPL, old, datetime.now(UTC), "1m")
    assert tickers == {}


# yfinance log noise ------------------------------------------------------


class NoisyTicker:
    """Logs like yfinance does for an unknown ticker, then returns nothing."""

    @property
    def info(self) -> dict[str, Any]:
        logging.getLogger("yfinance").error('HTTP Error 404: {"quoteSummary": "Quote not found"}')
        return {"trailingPegRatio": None}

    def history(self, **kwargs: Any) -> pd.DataFrame:
        logging.getLogger("yfinance").error("$NOPE: possibly delisted; no price data found")
        return pd.DataFrame()


def test_yfinance_errors_are_silenced_during_adapter_calls(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(yahoo.yf, "Ticker", lambda s: NoisyTicker())
    with pytest.raises(SymbolNotFound):
        YahooProvider().get_quote(NOPE)
    with pytest.raises(SymbolNotFound):
        YahooProvider().get_history(NOPE, START, END, "1d")
    assert caplog.records == []


def test_yfinance_logs_outside_adapter_calls_are_untouched(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(yahoo.yf, "Ticker", lambda s: NoisyTicker())
    with pytest.raises(SymbolNotFound):
        YahooProvider().get_quote(NOPE)
    _ = NoisyTicker().info
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_yfinance_debug_logging_disables_silencing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="yfinance")
    monkeypatch.setattr(yahoo.yf, "Ticker", lambda s: NoisyTicker())
    with pytest.raises(SymbolNotFound):
        YahooProvider().get_quote(NOPE)
    assert "Quote not found" in caplog.text
