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

    monkeypatch.setattr(socket, "socket", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    from agora_quotes import registry

    registry.configure(None)
