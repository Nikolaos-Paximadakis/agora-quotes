"""Real network calls; skipped unless AGORA_QUOTES_LIVE_TESTS=1."""

import asyncio
import os
from zoneinfo import ZoneInfo

import pytest

import agora_quotes as aq

pytestmark = pytest.mark.live


def test_live_greek_quote() -> None:
    q = aq.get_quote("EXAE.AT")
    assert q.currency == "EUR"
    assert q.delayed is True
    assert q.price > 0


def test_live_us_quote() -> None:
    q = aq.get_quote("AAPL")
    assert q.currency == "USD"
    assert q.price > 0


def test_live_unknown_symbol() -> None:
    result = aq.get_quotes(["AAPL", "NOPEZZZ.AT"])
    assert isinstance(result["NOPEZZZ.AT"], aq.SymbolNotFound)


def test_live_history_greek_daily() -> None:
    bars = aq.get_history("ATHEX:EXAE", start="2026-09-01", end="2026-10-01")
    assert len(bars) > 15
    assert all(b.symbol == "ATHEX:EXAE" and b.timestamp.tzinfo is not None for b in bars)


def test_live_history_is_not_split_adjusted() -> None:
    # AAPL split 4:1 on 2020-08-31 and closed at 499.23 the session before;
    # Yahoo itself serves 124.81 there (agora-quotes#9).
    bars = aq.get_history("AAPL", start="2020-08-28", end="2020-08-29")
    assert len(bars) == 1
    assert bars[0].close == pytest.approx(499.23, abs=0.01)


def test_live_greek_history_is_not_reverse_split_adjusted() -> None:
    # ETE (National Bank of Greece) reverse-split 1:10 on 2018-08-29. Bars are
    # picked by their Athens date: a daily bar starts at Athens midnight, which
    # is the previous day in UTC.
    athens = ZoneInfo("Europe/Athens")
    bars = aq.get_history("ATHEX:ETE", start="2018-08-20", end="2018-09-10")
    close = {b.timestamp.astimezone(athens).date().isoformat(): b.close for b in bars}
    assert close["2018-08-28"] == pytest.approx(0.2448, abs=0.0001)
    assert close["2018-09-03"] == pytest.approx(2.35, abs=0.01)


def test_live_yahoo_stream() -> None:
    async def first_quote() -> aq.Quote:
        # Bitcoin trades around the clock, so this works outside market hours.
        async for q in aq.stream(["BTC-USD"]):
            return q
        raise AssertionError("stream ended")

    q = asyncio.run(asyncio.wait_for(first_quote(), timeout=60))
    assert (q.symbol, q.source, q.currency, q.delayed) == ("BTC-USD", "yahoo", "USD", False)
    assert q.price > 0


@pytest.mark.skipif(not os.environ.get("TWELVEDATA_API_KEY"), reason="TWELVEDATA_API_KEY not set")
def test_live_twelvedata_us_quote_and_history() -> None:
    aq.configure(provider="twelvedata", cache_ttl=0)
    q = aq.get_quote("AAPL")
    assert (q.source, q.currency, q.delayed) == ("twelvedata", "USD", True)
    assert q.price > 0
    assert isinstance(aq.get_quotes(["NOPEZZZ"])["NOPEZZZ"], aq.SymbolNotFound)
    bars = aq.get_history("AAPL", start="2026-09-01", end="2026-10-01")
    assert len(bars) > 15
    assert all(b.source == "twelvedata" and b.timestamp.tzinfo is not None for b in bars)


@pytest.mark.skipif(not os.environ.get("TWELVEDATA_API_KEY"), reason="TWELVEDATA_API_KEY not set")
def test_live_twelvedata_stream() -> None:
    aq.configure(provider="twelvedata")

    async def first_quote() -> aq.Quote:
        async for q in aq.stream(["AAPL"]):
            return q
        raise AssertionError("stream ended")

    try:
        q = asyncio.run(asyncio.wait_for(first_quote(), timeout=30))
    except asyncio.TimeoutError:
        pytest.skip("no AAPL price update within 30s (market closed?)")
    assert (q.symbol, q.source, q.delayed) == ("AAPL", "twelvedata", True)
    assert q.price > 0


def _eodhd_key(monkeypatch: pytest.MonkeyPatch) -> str:
    # EODHD's public "demo" key covers a few US tickers (AAPL.US, TSLA.US, ...).
    key = os.environ.get("EODHD_API_KEY") or "demo"
    monkeypatch.setenv("EODHD_API_KEY", key)
    aq.configure(provider="eodhd", cache_ttl=0)
    return key


def test_live_eodhd_quotes_and_history(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _eodhd_key(monkeypatch)
    result = aq.get_quotes(["AAPL", "TSLA"])
    for sym, q in result.items():
        assert isinstance(q, aq.Quote), q
        assert (q.symbol, q.source, q.currency, q.delayed) == (sym, "eodhd", "USD", True)
        assert q.price > 0
    bars = aq.get_history("AAPL", start="2026-09-01", end="2026-10-01")
    assert len(bars) > 15
    assert all(b.source == "eodhd" and b.timestamp.tzinfo is not None for b in bars)
    if key == "demo":
        return  # the demo key answers 403 for every other ticker
    assert isinstance(aq.get_quotes(["NOPEZZZ"])["NOPEZZZ"], aq.SymbolNotFound)
    q = aq.get_quote("ATHEX:EXAE")
    assert (q.currency, q.delayed) == ("EUR", True)


def test_live_eodhd_intraday(monkeypatch: pytest.MonkeyPatch) -> None:
    _eodhd_key(monkeypatch)
    try:
        bars = aq.get_history(
            "AAPL", start="2026-09-21T14:30Z", end="2026-09-21T16:30Z", interval="15m"
        )
    except aq.ProviderError as e:
        if "only eod data" in str(e).lower():
            pytest.skip("the EODHD plan has no intraday data")
        raise
    assert len(bars) == 8
