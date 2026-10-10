"""The public operations: symbol parsing, caching and provider fallback."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import get_args

from agora_quotes import registry
from agora_quotes.errors import (
    AgoraQuotesError,
    ConfigurationError,
    InvalidSymbol,
    ProviderError,
    SymbolNotFound,
)
from agora_quotes.models import DAILY_INTERVALS, Bar, Interval, Quote
from agora_quotes.providers.base import StreamingProvider
from agora_quotes.symbols import Symbol, parse


def get_quote(symbol: str) -> Quote:
    """Return the latest quote for ``symbol``; raises ``AgoraQuotesError`` on failure.

    Tries the primary provider, then the fallback (if configured). Quotes are
    served from the cache while fresh.
    """
    sym = parse(symbol)
    cfg = registry.settings()
    cached = cfg.cache.get(sym)
    if cached is not None:
        return cached
    errors: list[AgoraQuotesError] = []
    for provider in cfg.providers:
        try:
            quote = provider.get_quote(sym)
        except AgoraQuotesError as e:
            errors.append(e)
            continue
        cfg.cache.set(sym, quote)
        return quote
    raise _pick_error(errors)


def get_quotes(symbols: Sequence[str]) -> dict[str, Quote | AgoraQuotesError]:
    """Return a quote or an error for each symbol, keyed by the symbol as passed.

    Only symbols the primary provider could not serve are retried on the
    fallback. Raises only when every provider failed as a whole (e.g. network
    down, rate limited).
    """
    cfg = registry.settings()
    results: dict[str, Quote | AgoraQuotesError] = {}
    wanted: dict[str, Symbol] = {}
    for raw in symbols:
        try:
            sym = parse(raw)
        except InvalidSymbol as e:
            results[raw] = e
            continue
        cached = cfg.cache.get(sym)
        if cached is not None:
            results[raw] = cached
        else:
            wanted[raw] = sym

    pending = set(wanted.values())
    found: dict[Symbol, Quote] = {}
    errors: dict[Symbol, list[AgoraQuotesError]] = {s: [] for s in pending}
    source_wide: list[AgoraQuotesError] = []
    for provider in cfg.providers:
        if not pending:
            break
        try:
            got = provider.get_quotes(sorted(pending, key=str))
        except AgoraQuotesError as e:
            source_wide.append(e)
            for sym in pending:
                errors[sym].append(e)
            continue
        for sym, value in got.items():
            if isinstance(value, Quote):
                cfg.cache.set(sym, value)
                found[sym] = value
                pending.discard(sym)
            else:
                errors[sym].append(value)

    if pending and len(source_wide) == len(cfg.providers):
        raise source_wide[0]
    for raw, sym in wanted.items():
        results[raw] = found[sym] if sym in found else _pick_error(errors[sym])
    return {raw: results[raw] for raw in symbols}


def get_history(
    symbol: str,
    start: str | date | datetime,
    end: str | date | datetime | None = None,
    interval: Interval = "1d",
) -> list[Bar]:
    """Return OHLCV bars from ``start`` up to ``end`` (``end`` defaults to now).

    Intraday bars satisfy ``start <= timestamp < end``. Daily and longer bars
    are picked by their trading date in exchange time, from ``start``'s date up
    to but excluding ``end``'s date, so ``start="2026-01-05", end="2026-01-06"``
    is the 5 January session on any exchange; an ``end`` after midnight
    includes that day, and a week or month counts if it overlaps the range.
    A datetime's date is taken in its own timezone. Intraday, dates and ISO
    strings mean midnight UTC. Datetimes must be timezone-aware. History is
    not cached.
    """
    if interval not in get_args(Interval):
        raise ValueError(f"interval must be one of {get_args(Interval)}, got {interval!r}")
    sym = parse(symbol)
    start_dt = _to_aware(start, "start")
    end_dt = datetime.now(timezone.utc) if end is None else _to_aware(end, "end")
    if start_dt >= end_dt:
        raise ValueError(f"start ({start_dt}) must be before end ({end_dt})")
    if interval in DAILY_INTERVALS:
        start_dt, end_dt = _whole_days(start_dt, end_dt)
    else:
        start_dt, end_dt = start_dt.astimezone(timezone.utc), end_dt.astimezone(timezone.utc)
    errors: list[AgoraQuotesError] = []
    for provider in registry.settings().providers:
        try:
            return provider.get_history(sym, start_dt, end_dt, interval)
        except AgoraQuotesError as e:
            errors.append(e)
    raise _pick_error(errors)


async def stream(symbols: Sequence[str]) -> AsyncGenerator[Quote, None]:
    """Yield a quote for every price update on ``symbols`` until cancelled.

    Uses the first configured provider (primary, then fallback) that can
    stream; there is no failover once the stream has started. Streamed quotes
    bypass the cache. Cancel the consuming task, or close the generator, to
    disconnect.
    """
    syms = [parse(s) for s in symbols]
    provider = next(
        (p for p in registry.settings().providers if isinstance(p, StreamingProvider)), None
    )
    if provider is None:
        names = [p.name for p in registry.settings().providers]
        raise ConfigurationError(f"none of the configured providers {names} can stream")
    quotes = provider.stream(syms)
    try:
        async for quote in quotes:
            yield quote
    finally:
        # ``async for`` doesn't close it when this generator is closed early.
        await quotes.aclose()


def _pick_error(errors: list[AgoraQuotesError]) -> AgoraQuotesError:
    """``SymbolNotFound`` only if every provider said so; otherwise the first real failure."""
    for e in errors:
        if not isinstance(e, SymbolNotFound):
            return e
    return errors[0] if errors else ProviderError("provider returned no result", "agora_quotes")


def _whole_days(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """UTC midnights spanning the caller's dates: ``start``'s date up to the
    date of the last instant before ``end``, each taken in its own timezone.

    Adapters read daily bounds back as UTC dates (``providers.base.bar_dates``),
    so ``datetime(2026, 1, 6, tzinfo=Athens)`` means 6 January, not the 5th.
    """
    first, last = start.date(), (end - timedelta(microseconds=1)).date()
    return (
        datetime.combine(first, time(), tzinfo=timezone.utc),
        datetime.combine(last + timedelta(days=1), time(), tzinfo=timezone.utc),
    )


def _to_aware(value: str | date | datetime, field: str) -> datetime:
    """A timezone-aware datetime, kept in its own timezone; a date is midnight UTC."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value) if "T" in value else date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{field} is not an ISO date/datetime: {value!r}") from None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must be timezone-aware, got naive {value!r}")
        return value
    return datetime.combine(value, time(), tzinfo=timezone.utc)
