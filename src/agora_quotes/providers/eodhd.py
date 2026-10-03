"""EODHD adapter (not implemented yet).

Planned: call the EODHD REST API with ``EODHD_API_KEY`` from the environment.
EODHD uses ``TICKER.EXCHANGE`` symbols (e.g. ``EXAE.AT``, ``AAPL.US``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agora_quotes.errors import AgoraQuotesError
from agora_quotes.models import Bar, Interval, Quote


class EODHDProvider:
    name = "eodhd"

    def get_quote(self, symbol: str) -> Quote:
        raise NotImplementedError("EODHD adapter is not implemented yet")

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote | AgoraQuotesError]:
        raise NotImplementedError("EODHD adapter is not implemented yet")

    def get_history(
        self, symbol: str, start: datetime, end: datetime | None, interval: Interval
    ) -> list[Bar]:
        raise NotImplementedError("EODHD adapter is not implemented yet")
