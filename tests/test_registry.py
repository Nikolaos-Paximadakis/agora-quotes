import pytest

import agora_quotes as aq
from agora_quotes import ConfigurationError, registry
from agora_quotes.providers.base import Provider, StreamingProvider
from agora_quotes.providers.eodhd import EODHDProvider
from agora_quotes.providers.twelvedata import TwelveDataProvider
from agora_quotes.providers.yahoo import YahooProvider


def test_default_provider_is_yahoo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGORA_QUOTES_PROVIDER", raising=False)
    assert isinstance(registry.get_provider(), YahooProvider)


def test_provider_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGORA_QUOTES_PROVIDER", "eodhd")
    assert isinstance(registry.get_provider(), EODHDProvider)


def test_unknown_provider_name() -> None:
    with pytest.raises(ConfigurationError, match="unknown provider"):
        aq.configure("bloomberg")


def test_all_adapters_satisfy_protocol() -> None:
    for cls in (YahooProvider, TwelveDataProvider, EODHDProvider):
        assert isinstance(cls(), Provider)
    assert isinstance(TwelveDataProvider(), StreamingProvider)


def test_stubs_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        TwelveDataProvider().get_quote("AAPL")
    with pytest.raises(NotImplementedError):
        EODHDProvider().get_quote("AAPL")
