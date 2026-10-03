"""Chooses the active provider.

The provider is picked by ``configure(provider=...)`` or, failing that, by the
``AGORA_QUOTES_PROVIDER`` environment variable (default ``yahoo``). Adapters
are imported lazily so that unused vendor SDKs need not be installed.
"""

from __future__ import annotations

import importlib
import os

from agora_quotes.errors import ConfigurationError
from agora_quotes.providers.base import Provider

# name -> "module:ClassName"
PROVIDERS: dict[str, str] = {
    "yahoo": "agora_quotes.providers.yahoo:YahooProvider",
    "twelvedata": "agora_quotes.providers.twelvedata:TwelveDataProvider",
    "eodhd": "agora_quotes.providers.eodhd:EODHDProvider",
}

_active: Provider | None = None


def create_provider(name: str) -> Provider:
    try:
        target = PROVIDERS[name]
    except KeyError:
        raise ConfigurationError(
            f"unknown provider {name!r}; choose from {sorted(PROVIDERS)}"
        ) from None
    module_name, class_name = target.split(":")
    cls = getattr(importlib.import_module(module_name), class_name)
    provider: Provider = cls()
    return provider


def configure(provider: str | Provider | None = None) -> None:
    """Set the active provider by name or instance; None re-reads the environment."""
    global _active
    _active = create_provider(provider) if isinstance(provider, str) else provider


def get_provider() -> Provider:
    global _active
    if _active is None:
        _active = create_provider(os.environ.get("AGORA_QUOTES_PROVIDER", "yahoo"))
    return _active
