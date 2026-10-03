"""Twelve Data streaming: the SDK's real threads, with a fake websocket underneath."""

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import datetime, timezone
from typing import Any, ClassVar

import pytest
import websocket
from twelvedata.websocket import EventHandler

from agora_quotes import ProviderError, Quote
from agora_quotes.providers import twelvedata
from agora_quotes.providers.twelvedata import TwelveDataProvider
from agora_quotes.symbols import Symbol

# _Socket.close ends the SDK's dispatch thread with SystemExit, which Python's
# default threading.excepthook ignores but pytest's reports.
pytestmark = pytest.mark.filterwarnings(
    r"ignore:(?s)Exception in thread.*\nSystemExit\n:pytest.PytestUnhandledThreadExceptionWarning"
)

EXAE = Symbol("EXAE", "ATHEX")
AAPL = Symbol("AAPL")


def price(symbol: str, mic: str, value: float, ts: int = 1790950792) -> dict[str, Any]:
    return {
        "event": "price",
        "symbol": symbol,
        "mic_code": mic,
        "currency": "EUR" if mic == "XATH" else "USD",
        "timestamp": ts,
        "price": value,
    }


class FakeApp:
    """Stands in for ``websocket.WebSocketApp``; runs on the SDK's receiver thread."""

    instances: ClassVar[list["FakeApp"]] = []
    fail_with: Exception | None = None  # makes every later connection attempt fail

    def __init__(self, url: str, **callbacks: Callable[..., None]) -> None:
        self.url = url
        self.callbacks = callbacks
        self.sent: list[dict[str, Any]] = []
        self.closed = threading.Event()
        FakeApp.instances.append(self)

    def run_forever(self, **kwargs: Any) -> None:
        if FakeApp.fail_with is not None:
            self.callbacks["on_error"](self, FakeApp.fail_with)
            return
        self.callbacks["on_open"](self)
        self.closed.wait()

    def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def close(self) -> None:
        self.closed.set()

    def push(self, event: dict[str, Any]) -> None:
        self.callbacks["on_message"](self, json.dumps(event))

    def fail(self, error: Exception) -> None:
        self.callbacks["on_error"](self, error)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> TwelveDataProvider:
    monkeypatch.setenv("TWELVEDATA_API_KEY", "test-key")
    monkeypatch.setattr(websocket, "WebSocketApp", FakeApp)
    monkeypatch.setattr(FakeApp, "instances", [])
    monkeypatch.setattr(FakeApp, "fail_with", None)
    monkeypatch.setattr(twelvedata, "RECONNECT_DELAY_S", 0)
    return TwelveDataProvider()


async def until(condition: Callable[[], bool], timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


async def opened(count: int = 1) -> FakeApp:
    """Wait until connection number ``count`` has opened and subscribed."""
    await until(lambda: len(FakeApp.instances) >= count and bool(FakeApp.instances[-1].sent))
    return FakeApp.instances[-1]


def run(main: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(asyncio.wait_for(main(), timeout=5))


def handler_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if isinstance(t, EventHandler) and t.is_alive()]


def test_stream_yields_quotes_for_subscribed_symbols(provider: TwelveDataProvider) -> None:
    async def main() -> None:
        stream = provider.stream([EXAE, AAPL])
        first = asyncio.ensure_future(anext(stream))
        app = await opened()
        assert "apikey=test-key" in app.url
        assert app.sent == [
            {
                "action": "subscribe",
                "params": {"symbols": [{"symbol": "AAPL"}, {"symbol": "EXAE", "mic_code": "XATH"}]},
            }
        ]
        app.push({"event": "subscribe-status", "status": "ok", "success": [], "fails": []})
        app.push(price("EXAE", "XATH", 10.22))
        app.push({"event": "heartbeat", "status": "ok"})
        app.push(price("AAPL", "XNAS", 333.5))  # subscribed without a MIC code
        app.push(price("OTHER", "XNAS", 1.0))  # not ours: ignored
        quotes = [await first, await anext(stream)]
        await stream.aclose()

        assert [(q.symbol, q.price, q.currency) for q in quotes] == [
            ("ATHEX:EXAE", 10.22, "EUR"),
            ("AAPL", 333.5, "USD"),
        ]
        q = quotes[0]
        assert q.timestamp == datetime.fromtimestamp(1790950792, tz=timezone.utc)
        assert (q.delayed, q.source) == (True, "twelvedata")
        assert q.retrieved_at.tzinfo is not None
        assert app.closed.is_set()

    run(main)


def test_cancelling_the_consumer_closes_the_socket_and_threads(
    provider: TwelveDataProvider,
) -> None:
    before = len(handler_threads())

    async def main() -> None:
        got: list[Quote] = []

        async def consume() -> None:
            async for q in provider.stream([EXAE]):
                got.append(q)

        task = asyncio.create_task(consume())
        app = await opened()
        app.push(price("EXAE", "XATH", 10.0))
        await until(lambda: len(got) == 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert app.closed.is_set()
        await until(lambda: len(handler_threads()) == before)

    run(main)


def test_rejected_subscription_raises_naming_the_symbol(provider: TwelveDataProvider) -> None:
    async def main() -> None:
        stream = provider.stream([EXAE, AAPL])
        first = asyncio.ensure_future(anext(stream))
        app = await opened()
        app.push(
            {
                "event": "subscribe-status",
                "status": "error",
                "success": [{"symbol": "AAPL", "mic_code": "XNAS"}],
                "fails": [{"symbol": "EXAE", "mic_code": "XATH"}],
            }
        )
        with pytest.raises(ProviderError, match=r"ATHEX:EXAE"):
            await first
        assert app.closed.is_set()

    run(main)


def test_connection_refused_before_opening_raises(provider: TwelveDataProvider) -> None:
    FakeApp.fail_with = ConnectionRefusedError("401 Unauthorized")

    async def main() -> None:
        with pytest.raises(ProviderError, match=r"could not connect.*401") as info:
            await anext(provider.stream([AAPL]))
        assert isinstance(info.value.__cause__, ConnectionRefusedError)
        assert len(FakeApp.instances) == 1  # no retry loop

    run(main)


def test_dropped_connection_reconnects_and_resubscribes(provider: TwelveDataProvider) -> None:
    async def main() -> None:
        stream = provider.stream([EXAE])
        first = asyncio.ensure_future(anext(stream))
        app = await opened()
        app.fail(ConnectionResetError("dropped"))
        second = await opened(2)
        assert second.sent == app.sent
        second.push(price("EXAE", "XATH", 10.5))
        assert (await first).price == 10.5
        await stream.aclose()

    run(main)


def test_gives_up_after_repeated_reconnect_failures(
    provider: TwelveDataProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(twelvedata, "MAX_RECONNECTS", 1)

    async def main() -> None:
        stream = provider.stream([EXAE])
        first = asyncio.ensure_future(anext(stream))
        app = await opened()
        FakeApp.fail_with = ConnectionRefusedError("still down")
        app.fail(ConnectionResetError("dropped"))
        with pytest.raises(ProviderError, match=r"could not reconnect.*still down"):
            await first

    run(main)


def test_heartbeats_continue_while_the_consumer_is_busy(
    provider: TwelveDataProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(twelvedata, "HEARTBEAT_S", 0.01)

    async def main() -> None:
        stream = provider.stream([AAPL])
        first = asyncio.ensure_future(anext(stream))
        app = await opened()
        app.push(price("AAPL", "XNAS", 1.0))
        await first  # the generator is now paused at yield, not pulling events
        await until(lambda: {"action": "heartbeat"} in app.sent)
        await stream.aclose()

    run(main)


def test_heartbeats_are_sent_while_idle(
    provider: TwelveDataProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(twelvedata, "HEARTBEAT_S", 0.01)

    async def main() -> None:
        stream = provider.stream([AAPL])
        first = asyncio.ensure_future(anext(stream))
        app = await opened()
        await until(lambda: {"action": "heartbeat"} in app.sent)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert app.closed.is_set()

    run(main)


def test_malformed_price_event_raises_provider_error(provider: TwelveDataProvider) -> None:
    async def main() -> None:
        stream: AsyncIterator[Quote] = provider.stream([AAPL])
        first = asyncio.ensure_future(anext(stream))
        app = await opened()
        app.push({"event": "price", "symbol": "AAPL", "mic_code": "XNAS", "price": "n/a"})
        with pytest.raises(ProviderError, match="bad price event"):
            await first

    run(main)
