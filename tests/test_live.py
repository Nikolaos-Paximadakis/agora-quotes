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
    result = aq.get_quotes(["AAPL", "NOPE_ZZZ.AT"])
    assert isinstance(result["NOPE_ZZZ.AT"], aq.SymbolNotFound)
