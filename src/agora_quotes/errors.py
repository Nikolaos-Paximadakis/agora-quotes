"""Typed exceptions. Providers wrap every vendor exception in one of these."""

from __future__ import annotations


class AgoraQuotesError(Exception):
    """Base class for all errors raised by agora_quotes."""


class ConfigurationError(AgoraQuotesError):
    """Unknown provider name, missing API key, or invalid setting."""


class SymbolNotFound(AgoraQuotesError):
    """The provider has no data for this symbol."""

    def __init__(self, symbol: str, source: str) -> None:
        super().__init__(f"{source}: symbol not found: {symbol}")
        self.symbol = symbol
        self.source = source


class ProviderError(AgoraQuotesError):
    """The upstream source failed (network, bad response, unexpected vendor error)."""

    def __init__(self, message: str, source: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source


class RateLimited(ProviderError):
    """The upstream source refused the request because of rate limiting."""

    def __init__(self, source: str, retry_after: float | None = None) -> None:
        super().__init__("rate limited", source)
        self.retry_after = retry_after
