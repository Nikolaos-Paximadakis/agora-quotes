"""Consistent stock quotes over pluggable data sources.

import agora_quotes as aq
aq.get_quote("EXAE.AT")
aq.get_quotes(["AAPL", "EXAE.AT"])
"""

from __future__ import annotations

from collections.abc import Sequence

from agora_quotes.errors import (
    AgoraQuotesError,
    ConfigurationError,
    ProviderError,
    RateLimited,
    SymbolNotFound,
)
from agora_quotes.models import Bar, Interval, Quote
from agora_quotes.registry import configure, get_provider

__all__ = [
    "AgoraQuotesError",
    "Bar",
    "ConfigurationError",
    "Interval",
    "ProviderError",
    "Quote",
    "RateLimited",
    "SymbolNotFound",
    "configure",
    "get_quote",
    "get_quotes",
]


def get_quote(symbol: str) -> Quote:
    """Return the latest quote for ``symbol``; raises ``AgoraQuotesError`` on failure."""
    return get_provider().get_quote(symbol)


def get_quotes(symbols: Sequence[str]) -> dict[str, Quote | AgoraQuotesError]:
    """Return a quote or an error for each symbol, keyed by the symbol as passed."""
    return get_provider().get_quotes(symbols)
