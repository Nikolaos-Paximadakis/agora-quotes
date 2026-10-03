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

- `__init__.py` is the public API: `get_quote`, `get_quotes`, `configure`, models and errors. It delegates to `registry.get_provider()`.
- `registry.py` maps a provider name to `"module:Class"` and imports adapters **lazily**, so vendor SDKs stay optional. The active provider comes from `configure()` or `AGORA_QUOTES_PROVIDER` (default `yahoo`).
- `providers/base.py` defines two Protocols:
  - `Provider` (sync): `get_quote`, `get_quotes`, `get_history`.
  - `StreamingProvider`: `stream()`, an **async generator**. Callback SDKs bridge in through an `asyncio.Queue`.
- `providers/yahoo.py` is the only real adapter. It reads `yf.Ticker(sym).info`:
  - price comes from `regularMarketPrice`, the market timestamp from `regularMarketTime`, and the delay from `exchangeDataDelayedBy`.
  - An unknown symbol doesn't raise in yfinance; `info` is nearly empty instead, and the adapter maps that to `SymbolNotFound`.
- `providers/twelvedata.py` and `providers/eodhd.py` are `NotImplementedError` stubs.

## Invariants (tests depend on these)

- **Delay honesty.** `Quote.delayed` is True unless the source explicitly says real-time; for Yahoo that means `exchangeDataDelayedBy == 0`, and a missing value counts as delayed. Never present delayed data as real-time.
- **Timestamps.** Models are frozen dataclasses (`slots=True`). Every datetime is timezone-aware and normalized to UTC in `__post_init__`; naive datetimes raise `ValueError`.
  - `Quote.timestamp` is the market time of the price.
  - `Quote.retrieved_at` is the time of the fetch.
- **Prices are `float`.** Bars are **unadjusted**.
- **Batches return errors inline.** `get_quotes` returns `dict[str, Quote | AgoraQuotesError]`, keyed by the symbol string as passed. Per-symbol failures are values; source-wide failures (`RateLimited`, `ProviderError`) raise.
- **No vendor exceptions escape.** Adapters wrap everything in `errors.py` types with `raise ... from e`.
- **API keys come only from environment variables.** Document new ones in `.env.example`. `.env` is gitignored.
- **No network in tests.** `tests/conftest.py` blocks sockets for every test unless it is marked `live`. Mock vendor SDKs instead (see `tests/test_yahoo.py`, which monkeypatches `yahoo.yf.Ticker`).

## Conventions

- Keep it small: no abstractions beyond what's needed now.
- Requires Python >= 3.10, so ruff targets py310 and mypy uses python_version 3.10. The local interpreter is 3.14.
- Make small, well-described git commits per milestone.

## Roadmap (milestone 2, not built yet)

- `get_history` returning `list[Bar]`.
- An in-process TTL quote cache. It is per process, not shared across apps.
- `symbols.py` normalization: `"ATHEX:EXAE"`, `"EXAE.AT"` and plain `"AAPL"` parse to `Symbol(ticker, exchange)`, and each provider formats its own native form.
- A fallback provider through `AGORA_QUOTES_FALLBACK`; in `get_quotes`, only failed symbols are retried.
- `AGORA_QUOTES_CACHE_TTL`.
