"""Real network calls; skipped unless AGORA_QUOTES_LIVE_TESTS=1."""

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
