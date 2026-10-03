"""Consistent stock quotes over pluggable data sources.

import agora_quotes as aq
aq.get_quote("EXAE.AT")
aq.get_quotes(["AAPL", "ATHEX:EXAE"])
aq.get_history("AAPL", start="2026-01-01", interval="1d")
"""

from __future__ import annotations

from agora_quotes.errors import (
    AgoraQuotesError,
    ConfigurationError,
    InvalidSymbol,
    ProviderError,
    RateLimited,
    SymbolNotFound,
)
from agora_quotes.models import Bar, Interval, Quote
from agora_quotes.registry import configure
from agora_quotes.service import get_history, get_quote, get_quotes
from agora_quotes.symbols import Symbol

__all__ = [
    "AgoraQuotesError",
    "Bar",
    "ConfigurationError",
    "Interval",
    "InvalidSymbol",
    "ProviderError",
    "Quote",
    "RateLimited",
    "Symbol",
    "SymbolNotFound",
    "configure",
    "get_history",
    "get_quote",
    "get_quotes",
]
