"""Yahoo streaming: yfinance's real AsyncWebSocket, with a fake connection underneath."""

import asyncio
import base64
import contextlib
import json
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timezone
from typing import Any

import pytest
import yfinance as yf
from test_yahoo import INFO
from yfinance.pricing_pb2 import PricingData

from agora_quotes import ProviderError, Quote, SymbolNotFound
from agora_quotes.providers import yahoo
from agora_quotes.providers.yahoo import YahooProvider
from agora_quotes.symbols import Symbol

EXAE = Symbol("EXAE", "ATHEX")
AAPL = Symbol("AAPL")
CLOSE = object()  # in a connection script: the server closes the connection


def msg(ticker: str, price: float, ms: int = 1790950792000, **fields: Any) -> str:
    data = PricingData(id=ticker, price=price, time=ms, market_hours=1)
    for k, v in fields.items():
        setattr(data, k, v)
    return json.dumps(
        {"type": "pricing", "message": base64.b64encode(data.SerializeToString()).decode()}
    )


class FakeConn:
    """Stands in for the ``websockets`` connection; plays its script, then stays open."""

    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def close(self) -> None:
        self.closed = True

    async def __aiter__(self) -> AsyncIterator[str]:
        for item in self.script:
            if item is CLOSE:
                return
            if isinstance(item, Exception):
                raise item
            yield item
        await asyncio.Event().wait()


@pytest.fixture
def conns(monkeypatch: pytest.MonkeyPatch) -> tuple[list[Any], list[FakeConn]]:
    """(scripts, made): each connection takes the next script; an exception fails it."""
    scripts: list[Any] = []
    made: list[FakeConn] = []

    class Socket(yf.AsyncWebSocket):  # type: ignore[misc]
        async def _connect(self) -> None:
            if self._ws is None:
                script = scripts.pop(0)
                if isinstance(script, Exception):
                    raise script
                self._ws = FakeConn(script)
                made.append(self._ws)

    class Ticker:
        def __init__(self, symbol: str) -> None:
            self.info = INFO.get(symbol, {"trailingPegRatio": None})

    monkeypatch.setattr(yahoo.yf, "AsyncWebSocket", Socket)
    monkeypatch.setattr(yahoo.yf, "Ticker", Ticker)
    monkeypatch.setattr(yahoo, "RECONNECT_DELAY_S", 0)
    return scripts, made


def take(symbols: Sequence[Symbol], n: int) -> list[Quote]:
    async def main() -> list[Quote]:
        out: list[Quote] = []
        async with contextlib.aclosing(YahooProvider().stream(symbols)) as quotes:
            async for q in quotes:
                out.append(q)
                if len(out) == n:
                    break
        return out

    return asyncio.run(asyncio.wait_for(main(), timeout=5))


def test_stream_yields_quotes_with_delay_from_info(conns: Any) -> None:
    scripts, made = conns
    scripts.append([msg("EXAE.AT", 10.25, currency="EUR"), msg("AAPL", 333.5, ms=1790971201500)])
    exae, aapl = take([EXAE, AAPL], 2)
    assert exae == Quote(
        symbol="ATHEX:EXAE",
        price=10.25,
        currency="EUR",
        timestamp=datetime.fromtimestamp(1790950792, tz=timezone.utc),
        delayed=True,
        source="yahoo",
        retrieved_at=exae.retrieved_at,
    )
    assert (aapl.symbol, aapl.price, aapl.delayed) == ("AAPL", 333.5, False)
    assert aapl.timestamp == datetime.fromtimestamp(1790971201.5, tz=timezone.utc)
    assert aapl.currency == "USD"  # not in the message; taken from info
    assert sorted(made[0].sent[0]["subscribe"]) == ["AAPL", "EXAE.AT"]
    assert made[0].closed


def test_stream_skips_other_sessions_unknown_ids_and_garbage(conns: Any) -> None:
    scripts, _ = conns
    scripts.append(
        [
            msg("AAPL", 1.0, market_hours=0),  # pre-market (omitted on the wire)
            msg("AAPL", 2.0, market_hours=2),  # post-market
            msg("MSFT", 3.0),
            "not json",
            json.dumps({"message": "%%%"}),
            msg("AAPL", 4.0),
        ]
    )
    assert [q.price for q in take([AAPL], 1)] == [4.0]


def test_stream_fans_out_one_yahoo_ticker_to_every_symbol(conns: Any) -> None:
    scripts, _ = conns
    scripts.append([msg("AAPL", 4.0)])
    quotes = take([AAPL, Symbol("AAPL", "NASDAQ"), AAPL], 2)
    assert [q.symbol for q in quotes] == ["AAPL", "NASDAQ:AAPL"]


def test_stream_rejects_unknown_symbols_before_connecting(conns: Any) -> None:
    _, made = conns
    with pytest.raises(SymbolNotFound, match="ATHEX:NOPE"):
        take([AAPL, Symbol("NOPE", "ATHEX")], 1)
    assert made == []


def test_stream_first_connection_failure_is_a_provider_error(conns: Any) -> None:
    scripts, _ = conns
    scripts.append(OSError("refused"))
    with pytest.raises(ProviderError, match="cannot connect"):
        take([AAPL], 1)


def test_stream_reconnects_and_resubscribes_after_a_drop(conns: Any) -> None:
    scripts, made = conns
    scripts += [[msg("AAPL", 1.0), OSError("reset")], OSError("down"), [msg("AAPL", 2.0)]]
    assert [q.price for q in take([AAPL], 2)] == [1.0, 2.0]
    assert [c.sent for c in made] == [[{"subscribe": ["AAPL"]}]] * 2
    assert all(c.closed for c in made)


def test_stream_gives_up_after_max_reconnects(conns: Any) -> None:
    scripts, _ = conns
    scripts += [[CLOSE]] + [OSError("down")] * yahoo.MAX_RECONNECTS
    with pytest.raises(ProviderError, match="reconnect attempts failed"):
        take([AAPL], 1)
    assert scripts == []


def test_stream_of_no_symbols_ends_immediately(conns: Any) -> None:
    assert take([], 1) == []
