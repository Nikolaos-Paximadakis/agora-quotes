"""Real network calls; skipped unless AGORA_QUOTES_LIVE_TESTS=1."""

import asyncio
import os

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


def test_live_eodhd_quotes_and_history(monkeypatch: pytest.MonkeyPatch) -> None:
    # EODHD's public "demo" key covers a few US tickers (AAPL.US, TSLA.US, ...).
    key = os.environ.get("EODHD_API_KEY") or "demo"
    monkeypatch.setenv("EODHD_API_KEY", key)
    aq.configure(provider="eodhd", cache_ttl=0)
    result = aq.get_quotes(["AAPL", "TSLA"])
    for sym, q in result.items():
        assert isinstance(q, aq.Quote), q
        assert (q.symbol, q.source, q.currency, q.delayed) == (sym, "eodhd", "USD", True)
        assert q.price > 0
    bars = aq.get_history("AAPL", start="2026-09-01", end="2026-10-01")
    assert len(bars) > 15
    assert all(b.source == "eodhd" and b.timestamp.tzinfo is not None for b in bars)
    bars = aq.get_history(
        "AAPL", start="2026-09-21T14:30Z", end="2026-09-21T16:30Z", interval="15m"
    )
    assert len(bars) == 8
    if key == "demo":
        return  # the demo key answers 403 for every other ticker
    assert isinstance(aq.get_quotes(["NOPEZZZ"])["NOPEZZZ"], aq.SymbolNotFound)
    q = aq.get_quote("ATHEX:EXAE")
    assert (q.currency, q.delayed) == ("EUR", True)
