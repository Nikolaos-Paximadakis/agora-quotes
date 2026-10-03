import os
import socket

import pytest

LIVE = os.environ.get("AGORA_QUOTES_LIVE_TESTS") == "1"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if LIVE:
        return
    skip = pytest.mark.skip(reason="set AGORA_QUOTES_LIVE_TESTS=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if "live" in request.keywords:
        return

    def guard(*args: object, **kwargs: object) -> None:
        raise RuntimeError("tests must not touch the network; mock the provider")

    class LocalOnlySocket(socket.socket):
        # asyncio needs local socketpairs for its event loop; block the rest.
        def connect(self, address: object) -> None:
            if self.family != socket.AF_UNIX:
                guard()
            super().connect(address)  # type: ignore[arg-type]

        def connect_ex(self, address: object) -> int:
            if self.family != socket.AF_UNIX:
                guard()
            return super().connect_ex(address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket, "socket", LocalOnlySocket)
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket, "getaddrinfo", guard)


@pytest.fixture(autouse=True)
def _clean_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from agora_quotes import registry

    for var in ("AGORA_QUOTES_PROVIDER", "AGORA_QUOTES_FALLBACK", "AGORA_QUOTES_CACHE_TTL"):
        monkeypatch.delenv(var, raising=False)
    registry.reset()
