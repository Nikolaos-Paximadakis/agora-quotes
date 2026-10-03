"""The public operations: symbol parsing, caching and provider fallback."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import date, datetime, time, timezone
from typing import get_args

from agora_quotes import registry
from agora_quotes.errors import (
    AgoraQuotesError,
    ConfigurationError,
    InvalidSymbol,
    ProviderError,
    SymbolNotFound,
)
from agora_quotes.models import Bar, Interval, Quote
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
    """Return OHLCV bars with ``start <= timestamp < end`` (``end`` defaults to now).

    Dates and ISO strings mean midnight UTC; datetimes must be timezone-aware.
    History is not cached.
    """
    if interval not in get_args(Interval):
        raise ValueError(f"interval must be one of {get_args(Interval)}, got {interval!r}")
    sym = parse(symbol)
    start_dt = _to_utc(start, "start")
    end_dt = datetime.now(timezone.utc) if end is None else _to_utc(end, "end")
    if start_dt >= end_dt:
        raise ValueError(f"start ({start_dt}) must be before end ({end_dt})")
    errors: list[AgoraQuotesError] = []
    for provider in registry.settings().providers:
        try:
            return provider.get_history(sym, start_dt, end_dt, interval)
        except AgoraQuotesError as e:
            errors.append(e)
    raise _pick_error(errors)


async def stream(symbols: Sequence[str]) -> AsyncIterator[Quote]:
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
    async for quote in provider.stream(syms):
        yield quote


def _pick_error(errors: list[AgoraQuotesError]) -> AgoraQuotesError:
    """``SymbolNotFound`` only if every provider said so; otherwise the first real failure."""
    for e in errors:
        if not isinstance(e, SymbolNotFound):
            return e
    return errors[0] if errors else ProviderError("provider returned no result", "agora_quotes")


def _to_utc(value: str | date | datetime, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value) if "T" in value else date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{field} is not an ISO date/datetime: {value!r}") from None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must be timezone-aware, got naive {value!r}")
        return value.astimezone(timezone.utc)
    return datetime.combine(value, time(), tzinfo=timezone.utc)
