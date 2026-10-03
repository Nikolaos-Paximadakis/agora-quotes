"""Provider selection, fallback and the shared quote cache.

Settings come from ``configure()``; anything not passed there is read from the
environment:

* ``AGORA_QUOTES_PROVIDER``: primary provider (default ``yahoo``)
* ``AGORA_QUOTES_FALLBACK``: optional provider tried when the primary fails
* ``AGORA_QUOTES_CACHE_TTL``: quote cache lifetime in seconds (default 60; 0 disables)

Adapters are imported lazily so that unused vendor SDKs need not be installed.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass

from agora_quotes.cache import TTLCache
from agora_quotes.errors import ConfigurationError
from agora_quotes.models import Quote
from agora_quotes.providers.base import Provider
from agora_quotes.symbols import Symbol

# name -> "module:ClassName"
PROVIDERS: dict[str, str] = {
    "yahoo": "agora_quotes.providers.yahoo:YahooProvider",
    "twelvedata": "agora_quotes.providers.twelvedata:TwelveDataProvider",
    "eodhd": "agora_quotes.providers.eodhd:EODHDProvider",
}
DEFAULT_CACHE_TTL = 60.0


@dataclass
class Settings:
    providers: list[Provider]  # primary first, then fallback
    cache: TTLCache[Symbol, Quote]


_settings: Settings | None = None


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


def configure(
    provider: str | Provider | None = None,
    fallback: str | Provider | None = None,
    cache_ttl: float | None = None,
) -> None:
    """Set the primary provider, an optional fallback and the cache TTL.

    Providers may be given by name or as instances. Arguments left as None are
    read from the environment. Reconfiguring empties the cache.
    """
    global _settings
    primary = _resolve(provider, "AGORA_QUOTES_PROVIDER", default="yahoo")
    assert primary is not None
    chain = [primary]
    second = _resolve(fallback, "AGORA_QUOTES_FALLBACK", default=None)
    if second is not None:
        chain.append(second)
    if cache_ttl is None:
        raw = os.environ.get("AGORA_QUOTES_CACHE_TTL", "")
        try:
            cache_ttl = float(raw) if raw.strip() else DEFAULT_CACHE_TTL
        except ValueError:
            raise ConfigurationError(f"AGORA_QUOTES_CACHE_TTL is not a number: {raw!r}") from None
    _settings = Settings(chain, TTLCache(cache_ttl))


def settings() -> Settings:
    if _settings is None:
        configure()
    assert _settings is not None
    return _settings


def reset() -> None:
    """Forget all configuration; the next call re-reads the environment."""
    global _settings
    _settings = None


def _resolve(value: str | Provider | None, env_var: str, default: str | None) -> Provider | None:
    if value is None:
        value = os.environ.get(env_var, "").strip() or default
    if value is None:
        return None
    return create_provider(value) if isinstance(value, str) else value
