"""Exchange-qualified symbols.

Callers may pass any of:

* ``"ATHEX:EXAE"`` / ``"NASDAQ:AAPL"`` (exchange-qualified, the canonical form)
* ``"EXAE.AT"`` (Yahoo-style suffix, for the exchanges in ``YAHOO_SUFFIXES``)
* ``"AAPL"`` (plain; ``exchange`` is None, meaning the provider's default, i.e. US)

Each provider converts a ``Symbol`` to its own native format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agora_quotes.errors import InvalidSymbol

# Canonical exchange code -> Yahoo ticker suffix ("" = no suffix, US listings).
YAHOO_SUFFIXES: dict[str, str] = {
    "ATHEX": ".AT",
    "NASDAQ": "",
    "NYSE": "",
    "LSE": ".L",
    "XETRA": ".DE",
}
_EXCHANGE_BY_SUFFIX = {suffix: exch for exch, suffix in YAHOO_SUFFIXES.items() if suffix}
_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-]*")


@dataclass(frozen=True, slots=True)
class Symbol:
    ticker: str
    exchange: str | None = None

    def __str__(self) -> str:
        return f"{self.exchange}:{self.ticker}" if self.exchange else self.ticker


def parse(raw: str) -> Symbol:
    """Parse a caller-supplied symbol; raises ``InvalidSymbol``."""
    text = raw.strip().upper()
    if ":" in text:
        exchange, _, ticker = text.partition(":")
        if exchange not in YAHOO_SUFFIXES:
            raise InvalidSymbol(raw, f"unknown exchange {exchange!r}")
    else:
        exchange, ticker = "", text
        base, dot, suffix = text.rpartition(".")
        # Only known suffixes mean an exchange; "BRK.B" stays a plain ticker.
        if dot and base and f".{suffix}" in _EXCHANGE_BY_SUFFIX:
            exchange, ticker = _EXCHANGE_BY_SUFFIX[f".{suffix}"], base
    if not _TICKER.fullmatch(ticker):
        raise InvalidSymbol(raw, "bad ticker")
    return Symbol(ticker, exchange or None)
