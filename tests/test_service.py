import asyncio
import contextlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeProvider, FakeStreamer

import agora_quotes as aq
from agora_quotes import (
    ConfigurationError,
    InvalidSymbol,
    ProviderError,
    RateLimited,
    SymbolNotFound,
)

UTC = timezone.utc


def setup(*providers: FakeProvider, ttl: float = 60) -> None:
    aq.configure(
        provider=providers[0], fallback=providers[1] if len(providers) > 1 else None, cache_ttl=ttl
    )


# get_quote ---------------------------------------------------------------


def test_quote_symbol_is_canonical_for_any_input_form() -> None:
    setup(FakeProvider("p", {"ATHEX:EXAE": 10.0}))
    assert aq.get_quote("EXAE.AT").symbol == "ATHEX:EXAE"
    assert aq.get_quote("athex:exae").price == 10.0


def test_quote_is_cached_across_input_forms() -> None:
    p = FakeProvider("p", {"ATHEX:EXAE": 10.0})
    setup(p)
    aq.get_quote("EXAE.AT")
    aq.get_quote("ATHEX:EXAE")
    assert len(p.calls) == 1


def test_cache_disabled() -> None:
    p = FakeProvider("p", {"AAPL": 1.0})
    setup(p, ttl=0)
    aq.get_quote("AAPL")
    aq.get_quote("AAPL")
    assert len(p.calls) == 2


def test_fallback_on_provider_error() -> None:
    primary = FakeProvider("primary", {}, fail=RateLimited("primary"))
    setup(primary, FakeProvider("backup", {"AAPL": 1.0}))
    assert aq.get_quote("AAPL").source == "backup"


def test_fallback_when_primary_lacks_symbol() -> None:
    setup(FakeProvider("primary", {}), FakeProvider("backup", {"AAPL": 1.0}))
    assert aq.get_quote("AAPL").source == "backup"


def test_no_fallback_call_when_primary_succeeds() -> None:
    backup = FakeProvider("backup", {"AAPL": 2.0})
    setup(FakeProvider("primary", {"AAPL": 1.0}), backup)
    assert aq.get_quote("AAPL").source == "primary"
    assert backup.calls == []


def test_not_found_everywhere_raises_symbol_not_found() -> None:
    setup(FakeProvider("primary", {}), FakeProvider("backup", {}))
    with pytest.raises(SymbolNotFound):
        aq.get_quote("AAPL")


def test_real_failure_wins_over_not_found() -> None:
    setup(
        FakeProvider("primary", {}, fail=ProviderError("down", "primary")),
        FakeProvider("backup", {}),
    )
    with pytest.raises(ProviderError, match="down"):
        aq.get_quote("AAPL")


def test_invalid_symbol_raises_before_calling_providers() -> None:
    p = FakeProvider("p", {})
    setup(p)
    with pytest.raises(InvalidSymbol):
        aq.get_quote("FOO:BAR")
    assert p.calls == []


# get_quotes --------------------------------------------------------------


def test_get_quotes_keys_and_order_follow_input() -> None:
    setup(FakeProvider("p", {"AAPL": 1.0, "ATHEX:EXAE": 10.0}))
    result = aq.get_quotes(["EXAE.AT", "FOO:BAR", "AAPL", "NOPE"])
    assert list(result) == ["EXAE.AT", "FOO:BAR", "AAPL", "NOPE"]
    assert isinstance(result["FOO:BAR"], InvalidSymbol)
    assert isinstance(result["NOPE"], SymbolNotFound)
    assert result["EXAE.AT"].price == 10.0  # type: ignore[union-attr]


def test_get_quotes_retries_only_failed_symbols_on_fallback() -> None:
    backup = FakeProvider("backup", {"AAPL": 2.0, "MSFT": 3.0})
    setup(FakeProvider("primary", {"AAPL": 1.0}), backup)
    result = aq.get_quotes(["AAPL", "MSFT"])
    assert result["AAPL"].source == "primary"  # type: ignore[union-attr]
    assert result["MSFT"].source == "backup"  # type: ignore[union-attr]
    assert backup.calls == [("get_quotes", ["MSFT"])]


def test_get_quotes_uses_and_fills_cache() -> None:
    p = FakeProvider("p", {"AAPL": 1.0, "MSFT": 3.0})
    setup(p)
    aq.get_quote("AAPL")
    aq.get_quotes(["AAPL", "MSFT"])
    aq.get_quotes(["AAPL", "MSFT"])
    assert p.calls == [("get_quote", ["AAPL"]), ("get_quotes", ["MSFT"])]


def test_get_quotes_works_with_cache_disabled() -> None:
    setup(FakeProvider("p", {"AAPL": 1.0}), ttl=0)
    assert aq.get_quotes(["AAPL"])["AAPL"].price == 1.0  # type: ignore[union-attr]


def test_get_quotes_duplicate_inputs_fetch_once() -> None:
    p = FakeProvider("p", {"ATHEX:EXAE": 10.0})
    setup(p)
    result = aq.get_quotes(["EXAE.AT", "ATHEX:EXAE"])
    assert set(result) == {"EXAE.AT", "ATHEX:EXAE"}
    assert p.calls == [("get_quotes", ["ATHEX:EXAE"])]


def test_get_quotes_raises_when_every_provider_fails_wholesale() -> None:
    setup(
        FakeProvider("primary", {}, fail=RateLimited("primary")),
        FakeProvider("backup", {}, fail=ProviderError("down", "backup")),
    )
    with pytest.raises(RateLimited):
        aq.get_quotes(["AAPL"])


def test_get_quotes_inline_error_when_primary_down_and_backup_lacks_symbol() -> None:
    setup(
        FakeProvider("primary", {}, fail=RateLimited("primary")),
        FakeProvider("backup", {"AAPL": 2.0}),
    )
    result = aq.get_quotes(["AAPL", "MSFT"])
    assert result["AAPL"].source == "backup"  # type: ignore[union-attr]
    assert isinstance(result["MSFT"], RateLimited)


# get_history -------------------------------------------------------------


def test_history_date_coercion() -> None:
    p = FakeProvider("p", {"AAPL": 1.0})
    setup(p)
    aq.get_history("AAPL", start="2026-01-01", end=date(2026, 2, 1))
    aq.get_history(
        "AAPL",
        start=datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=2))),
        end="2026-02-01T00:00:00+00:00",
    )
    assert p.calls[0][1][1:] == ["2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"]
    # Daily bounds are whole days: the datetime's date, in its own timezone.
    assert p.calls[1][1][1] == "2026-01-01T00:00:00+00:00"


def test_daily_bounds_take_a_datetimes_date_in_its_own_timezone() -> None:
    # Athens midnight of 6 January is 22:00Z on the 5th; the caller means the 6th.
    p = FakeProvider("p", {"AAPL": 1.0})
    setup(p)
    athens = ZoneInfo("Europe/Athens")
    aq.get_history(
        "AAPL",
        start=datetime(2026, 1, 6, tzinfo=athens),
        end=datetime(2026, 1, 7, tzinfo=athens),
    )
    aq.get_history(
        "AAPL",
        start=datetime(2026, 1, 6, 10, tzinfo=athens),
        end=datetime(2026, 1, 6, 15, tzinfo=athens),
    )
    whole_day = ["2026-01-06T00:00:00+00:00", "2026-01-07T00:00:00+00:00"]
    assert p.calls[0][1][1:] == whole_day
    assert p.calls[1][1][1:] == whole_day


def test_intraday_bounds_stay_instants() -> None:
    p = FakeProvider("p", {"AAPL": 1.0})
    setup(p)
    aq.get_history(
        "AAPL",
        start=datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=2))),
        end="2026-01-01T14:00:00+00:00",
        interval="1h",
    )
    assert p.calls[0][1][1:] == ["2026-01-01T10:00:00+00:00", "2026-01-01T14:00:00+00:00"]


def test_history_end_defaults_to_now() -> None:
    p = FakeProvider("p", {"AAPL": 1.0})
    setup(p)
    aq.get_history("AAPL", start="2026-01-01", interval="1h")
    end = datetime.fromisoformat(p.calls[0][1][2])
    assert abs(end - datetime.now(UTC)) < timedelta(seconds=5)
    # Daily: through today's (UTC) date.
    aq.get_history("AAPL", start="2026-01-01")
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    assert p.calls[1][1][2] == f"{tomorrow.isoformat()}T00:00:00+00:00"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"start": datetime(2026, 1, 1)}, "timezone-aware"),
        ({"start": "01/01/2026"}, "ISO"),
        ({"start": "2026-02-01", "end": "2026-01-01"}, "before"),
        ({"start": "2026-01-01", "interval": "2h"}, "interval"),
    ],
)
def test_history_rejects_bad_arguments(kwargs: dict[str, object], match: str) -> None:
    setup(FakeProvider("p", {"AAPL": 1.0}))
    with pytest.raises(ValueError, match=match):
        aq.get_history("AAPL", **kwargs)  # type: ignore[arg-type]


def test_history_falls_back() -> None:
    setup(
        FakeProvider("primary", {}, fail=RateLimited("primary")),
        FakeProvider("backup", {"AAPL": 1.0}),
    )
    assert aq.get_history("AAPL", start="2026-01-01")[0].source == "backup"


# stream ------------------------------------------------------------------


def collect(symbols: list[str]) -> list[aq.Quote]:
    async def run() -> list[aq.Quote]:
        return [q async for q in aq.stream(symbols)]

    return asyncio.run(run())


def test_stream_uses_the_first_provider_that_can_stream() -> None:
    primary = FakeProvider("p", {"AAPL": 1.0})
    streamer = FakeStreamer("s", {"AAPL": 2.0, "ATHEX:EXAE": 10.0})
    setup(primary, streamer)
    quotes = collect(["EXAE.AT", "AAPL"])
    assert [(q.symbol, q.source) for q in quotes] == [("ATHEX:EXAE", "s"), ("AAPL", "s")]
    assert streamer.calls == [("stream", ["ATHEX:EXAE", "AAPL"]), ("stream closed", [])]
    assert primary.calls == []


def test_closing_the_stream_early_closes_the_provider_stream() -> None:
    streamer = FakeStreamer("s", {"AAPL": 2.0, "ATHEX:EXAE": 10.0})
    setup(streamer)

    async def main() -> None:
        async with contextlib.aclosing(aq.stream(["AAPL", "EXAE.AT"])) as quotes:
            async for _ in quotes:
                break
        assert streamer.calls[-1] == ("stream closed", [])

    asyncio.run(main())


def test_stream_without_a_streaming_provider_is_a_configuration_error() -> None:
    setup(FakeProvider("p", {}))
    with pytest.raises(ConfigurationError, match="can stream"):
        collect(["AAPL"])


def test_stream_rejects_invalid_symbols_before_connecting() -> None:
    streamer = FakeStreamer("s", {})
    setup(streamer)
    with pytest.raises(InvalidSymbol):
        collect(["NOPE:AAPL"])
    assert streamer.calls == []
