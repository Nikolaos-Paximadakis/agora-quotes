import pytest

from agora_quotes import InvalidSymbol
from agora_quotes.providers.yahoo import yahoo_symbol
from agora_quotes.symbols import Symbol, parse


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ATHEX:EXAE", Symbol("EXAE", "ATHEX")),
        ("athex:exae", Symbol("EXAE", "ATHEX")),
        ("EXAE.AT", Symbol("EXAE", "ATHEX")),
        (" exae.at ", Symbol("EXAE", "ATHEX")),
        ("NASDAQ:AAPL", Symbol("AAPL", "NASDAQ")),
        ("AAPL", Symbol("AAPL")),
        ("BRK.B", Symbol("BRK.B")),  # unknown suffix: stays a plain ticker
        ("VOD.L", Symbol("VOD", "LSE")),
        ("SAP.DE", Symbol("SAP", "XETRA")),
    ],
)
def test_parse(raw: str, expected: Symbol) -> None:
    assert parse(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "FOO:BAR", "ATHEX:", ".AT", "AA PL", "A$"])
def test_parse_rejects(raw: str) -> None:
    with pytest.raises(InvalidSymbol):
        parse(raw)


def test_invalid_symbol_is_also_value_error() -> None:
    with pytest.raises(ValueError):
        parse("FOO:BAR")


@pytest.mark.parametrize(
    ("symbol", "canonical", "yahoo"),
    [
        (Symbol("EXAE", "ATHEX"), "ATHEX:EXAE", "EXAE.AT"),
        (Symbol("AAPL", "NASDAQ"), "NASDAQ:AAPL", "AAPL"),
        (Symbol("AAPL"), "AAPL", "AAPL"),
        (Symbol("VOD", "LSE"), "LSE:VOD", "VOD.L"),
    ],
)
def test_formatting(symbol: Symbol, canonical: str, yahoo: str) -> None:
    assert str(symbol) == canonical
    assert yahoo_symbol(symbol) == yahoo
