# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

`agora-quotes` (import `agora_quotes`) is a private package that gives the user's other Python apps one interface for stock quotes: ATHEX first, plus US and other markets. It is a **thin wrapper over existing libraries and APIs**. Never write scrapers or exchange feed handlers here. Consuming apps must never import yfinance or a vendor SDK directly.

## Commands

```bash
uv sync                                          # .venv + editable install + dev group
uv run pytest                                    # full suite; never touches the network
uv run pytest tests/test_yahoo.py::test_get_quotes_returns_errors_inline
AGORA_QUOTES_LIVE_TESTS=1 uv run pytest -m live  # real yfinance calls
uv run ruff check src tests
uv run ruff format src tests
uv run mypy                                      # --strict over src/ (config in pyproject)
```

## Architecture

Flow: `__init__.py` (re-exports) → `service.py` → `registry.settings()` → `providers/*`.

- **`symbols.py`** has `parse()`, which turns caller strings (`ATHEX:EXAE`, `EXAE.AT`, `AAPL`) into a frozen, hashable `Symbol(ticker, exchange|None)`. `str(Symbol)` is the canonical form. Unparseable input raises `InvalidSymbol`.
- **`service.py`** holds `get_quote`/`get_quotes`/`get_history`. It parses symbols, consults the cache (quotes only), and walks the provider chain.
  - `get_quotes` retries only the still-failing symbols on the next provider.
  - `_pick_error` returns `SymbolNotFound` only if every provider said so; otherwise it returns the first real failure.
  - `get_history` coerces `start`/`end` to UTC datetimes.
- **`registry.py`** builds `Settings(providers=[primary, fallback?], cache)` from `configure()` args or the environment, read lazily on first use. `reset()` forgets it, and tests call it via the autouse fixture in `conftest.py`. Adapters are listed as `"module:Class"` strings and imported **lazily**, so vendor SDKs stay optional.
- **`cache.py`** is `TTLCache`, keyed by `Symbol`. It is per process, not shared between apps.
- **`providers/base.py`** defines the `Provider` Protocol (sync; it receives `Symbol`s, not strings) and `StreamingProvider` (`stream()` is an **async generator**; callback SDKs bridge in via `asyncio.Queue`).
- **`providers/yahoo.py`**, the only real adapter:
  - `yahoo_symbol()` maps a Symbol to a Yahoo ticker.
  - Quotes come from `Ticker.info` (`regularMarketPrice`, `regularMarketTime`, `exchangeDataDelayedBy`).
  - History comes from `Ticker.history(auto_adjust=False)`.
- **yfinance quirks the Yahoo adapter handles.** yfinance never raises for bad symbols. `info` comes back nearly empty, and `history` returns an empty frame both for unknown symbols and for no-trading ranges. So the adapter checks `info` when `history` is empty, and rejects too-old intraday ranges up front using `INTRADAY_MAX_AGE`.
- **`providers/twelvedata.py`, `providers/eodhd.py`** are `NotImplementedError` stubs.

## Invariants (tests depend on these)

- **Delay honesty.** `Quote.delayed` is True unless the source explicitly says real-time; for Yahoo that means `exchangeDataDelayedBy == 0`, and a missing value counts as delayed. Never present delayed data as real-time.
- **Timestamps.** Models are frozen dataclasses (`slots=True`). Every datetime is timezone-aware and normalized to UTC in `__post_init__`; naive datetimes raise `ValueError`.
  - `Quote.timestamp` is the market time of the price.
  - `Quote.retrieved_at` is the time of the fetch.
- **Prices are `float`.** Bars are **unadjusted**.
- **Batches return errors inline.** `get_quotes` returns `dict[str, Quote | AgoraQuotesError]`, keyed by the symbol string as passed and in input order. `Quote.symbol` is canonical. Per-symbol failures are values; source-wide failures (`RateLimited`, `ProviderError`) raise.
- **No vendor exceptions escape.** Adapters wrap everything in `errors.py` types with `raise ... from e`.
- **API keys come only from environment variables.** Document new ones in `.env.example`. `.env` is gitignored.
- **No network in tests.** `tests/conftest.py` blocks sockets for every test unless it is marked `live`. Mock vendor SDKs instead (`tests/test_yahoo.py` monkeypatches `yahoo.yf.Ticker`). To test the service layer, use `tests/fakes.py:FakeProvider`, which records calls.

## Conventions

- Keep it small: no abstractions beyond what's needed now.
- Requires Python >= 3.10, so ruff targets py310 and mypy uses python_version 3.10. The local interpreter is 3.14.
- Make small, well-described git commits per milestone.

## Remaining work

GitHub issues are the task tracker (`gh issue list`). Don't keep a task list in this file.
