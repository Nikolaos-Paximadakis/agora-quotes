import pytest

import agora_quotes as aq
from agora_quotes import ConfigurationError, registry
from agora_quotes.providers.base import Provider, StreamingProvider
from agora_quotes.providers.eodhd import EODHDProvider
from agora_quotes.providers.twelvedata import TwelveDataProvider
from agora_quotes.providers.yahoo import YahooProvider
from agora_quotes.symbols import Symbol


def names() -> list[str]:
    return [p.name for p in registry.settings().providers]


def test_defaults() -> None:
    assert names() == ["yahoo"]
    assert registry.settings().cache.ttl == registry.DEFAULT_CACHE_TTL


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGORA_QUOTES_PROVIDER", "eodhd")
    monkeypatch.setenv("AGORA_QUOTES_FALLBACK", "yahoo")
    monkeypatch.setenv("AGORA_QUOTES_CACHE_TTL", "5")
    assert names() == ["eodhd", "yahoo"]
    assert registry.settings().cache.ttl == 5


def test_code_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGORA_QUOTES_PROVIDER", "eodhd")
    monkeypatch.setenv("TWELVEDATA_API_KEY", "test-key")
    aq.configure(provider="yahoo", fallback="twelvedata", cache_ttl=0)
    assert names() == ["yahoo", "twelvedata"]
    assert registry.settings().cache.ttl == 0


def test_configure_accepts_instances() -> None:
    p = YahooProvider()
    aq.configure(provider=p)
    assert registry.settings().providers == [p]


def test_unknown_provider_name() -> None:
    with pytest.raises(ConfigurationError, match="unknown provider"):
        aq.configure("bloomberg")


def test_bad_cache_ttl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGORA_QUOTES_CACHE_TTL", "soon")
    with pytest.raises(ConfigurationError, match="CACHE_TTL"):
        registry.settings()


def test_all_adapters_satisfy_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWELVEDATA_API_KEY", "test-key")
    for cls in (YahooProvider, TwelveDataProvider, EODHDProvider):
        assert isinstance(cls(), Provider)
    assert isinstance(TwelveDataProvider(), StreamingProvider)


def test_stubs_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        EODHDProvider().get_quote(Symbol("AAPL"))
