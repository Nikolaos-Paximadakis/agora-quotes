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
- **`service.py`** holds `get_quote`/`get_quotes`/`get_history`/`stream`. It parses symbols, consults the cache (quotes only), and walks the provider chain.
  - `get_quotes` retries only the still-failing symbols on the next provider.
  - `_pick_error` returns `SymbolNotFound` only if every provider said so; otherwise it returns the first real failure.
  - `get_history` coerces `start`/`end` to UTC datetimes.
  - `stream` uses the first provider in the chain that is a `StreamingProvider`. It does not fail over mid-stream and bypasses the cache.
- **`registry.py`** builds `Settings(providers=[primary, fallback?], cache)` from `configure()` args or the environment, read lazily on first use. `reset()` forgets it, and tests call it via the autouse fixture in `conftest.py`. Adapters are listed as `"module:Class"` strings and imported **lazily**, so vendor SDKs stay optional. A missing SDK raises `ConfigurationError` naming the extra to install.
- **`cache.py`** has `TTLCache` (in memory, per process) and `SQLiteCache` (a file shared between apps, chosen when `AGORA_QUOTES_CACHE_PATH` is set and the TTL is > 0). Both are keyed by `Symbol` and share `get`/`set`/`clear`/`ttl`. `SQLiteCache` stores wall-clock expiry plus the quote as JSON, opens a connection per operation, raises `ConfigurationError` if the file can't be opened at construction, and afterwards logs database errors and treats them as misses.
- **`providers/base.py`** defines the `Provider` Protocol (sync; it receives `Symbol`s, not strings) and `StreamingProvider` (`stream()` is an **async generator**; callback SDKs bridge in via `asyncio.Queue`).
- **`providers/yahoo.py`**, the only real adapter:
  - `yahoo_symbol()` maps a Symbol to a Yahoo ticker.
  - Quotes come from `Ticker.info` (`regularMarketPrice`, `regularMarketTime`, `exchangeDataDelayedBy`).
  - History comes from `Ticker.history(auto_adjust=False)`.
  - `stream()` uses yfinance's `AsyncWebSocket` only to connect, subscribe, send heartbeats and decode (`_ws`, `_decode_message`); it runs its own receive loop because `listen()` swallows errors, never really reconnects, and hot-spins after a clean close. The websocket ignores unknown tickers and has no delay field, so `stream()` first fetches `info` per symbol (unknown → `SymbolNotFound`; `delayed` from `exchangeDataDelayedBy`). It yields only `market_hours == 1` (regular session; proto3 omits 0 = pre-market). A drop reconnects and resubscribes on a new socket; `MAX_RECONNECTS` consecutive reconnects without a message → `ProviderError`. Tests subclass the real `AsyncWebSocket` over a fake connection (`tests/test_yahoo_stream.py`).
- **yfinance quirks the Yahoo adapter handles.** yfinance never raises for bad symbols. `info` comes back nearly empty, and `history` returns an empty frame both for unknown symbols and for no-trading ranges. So the adapter checks `info` when `history` is empty, and rejects too-old intraday ranges up front using `INTRADAY_MAX_AGE`.
- **`providers/twelvedata.py`** (optional extra `agora-quotes[twelvedata]`; also in the dev group):
  - Sends the ticker plus a MIC code from `MIC_CODES` (`ATHEX` → `XATH`); plain tickers mean US.
  - Calls `/quote` and `/time_series` through the client's `DefaultHttpClient`. `TDClient()` is avoided because constructing it makes a network request. `_HttpClient` keeps the API error code (429 → `RateLimited`, 404/400 symbol → `SymbolNotFound`, 403 plan → per-symbol `ProviderError`).
  - Quotes are always `delayed=True`: the API never reports real-time. History uses `adjust=none`; `end_date` is inclusive upstream, and daily+ bars start at midnight in the exchange's timezone (same as Yahoo).
  - `get_quotes` makes one request per symbol. On a 429 after at least one success, it returns what it has and puts `RateLimited` inline for the rest.
  - `stream()` wraps the SDK's `TDWebSocket` (it needs `websocket-client`, which the SDK doesn't declare, so the extra adds it). The subclass `_Socket` fills gaps in the SDK client:
    - It subscribes with `{"symbol", "mic_code"}` objects.
    - It reports a failed first connection, or more than `MAX_RECONNECTS` failed reconnects, as `ProviderError` instead of retrying forever.
    - On close it ends the SDK's dispatch thread by raising `SystemExit` from `on_event`. That is the only way out of the SDK's loop; the tests filter pytest's warning about it.
  - Events cross from the SDK threads via `loop.call_soon_threadsafe` into an `asyncio.Queue`. A separate task sends a heartbeat every `HEARTBEAT_S`, even while the consumer is busy, and the generator closes the socket in `finally`. `service.stream` calls `aclose()` on the provider generator itself, because `async for` does not. A rejected subscription raises; it is not skipped.
- **`providers/eodhd.py`** uses the stdlib (`urllib`), so it needs no extra. Requires `EODHD_API_KEY`.
  - `eodhd_symbol()` gives `TICKER.EXCHANGE` via `EXCHANGE_CODES` (`ATHEX` → `AT`); plain tickers mean `.US`.
  - Quotes come from `/real-time`, batched `BATCH_SIZE` per request (`real-time/FIRST?s=REST`). They are always `delayed=True`, and currency comes from the static `CURRENCIES` map because the API doesn't send one.
  - Unknown tickers come back as rows of `"NA"` or as a 404; both mean `SymbolNotFound`. A 403 (ticker outside the plan) fails the whole batch, so the adapter then retries per symbol and puts the 403 inline. 429 and 402 (daily quota) → `RateLimited`.
  - History: daily and longer bars come from `/eod` (`close` is unadjusted; `to` is an inclusive date). Shorter intervals come from `/intraday` (unix bounds, both inclusive), and ranges longer than `INTRADAY_MAX_SPAN` are rejected up front. The public `demo` key covers AAPL.US/TSLA.US, so the live test uses it when no key is set.

## Invariants (tests depend on these)

- **Delay honesty.** `Quote.delayed` is True unless the source explicitly says real-time; for Yahoo that means `exchangeDataDelayedBy == 0`, and a missing value counts as delayed. Never present delayed data as real-time.
- **Timestamps.** Models are frozen dataclasses (`slots=True`). Every datetime is timezone-aware and normalized to UTC in `__post_init__`; naive datetimes raise `ValueError`.
  - `Quote.timestamp` is the market time of the price.
  - `Quote.retrieved_at` is the time of the fetch.
- **Prices are `float`.** Bars are **unadjusted**.
- **Batches return errors inline.** `get_quotes` returns `dict[str, Quote | AgoraQuotesError]`, keyed by the symbol string as passed and in input order. `Quote.symbol` is canonical. Per-symbol failures are values; source-wide failures (`RateLimited`, `ProviderError`) raise.
- **No vendor exceptions escape.** Adapters wrap everything in `errors.py` types with `raise ... from e`.
- **API keys come only from environment variables.** Document new ones in `.env.example`. `.env` is gitignored.
- **No network in tests.** `tests/conftest.py` blocks DNS and non-Unix socket connects for every test unless it is marked `live`; local socketpairs stay allowed so `asyncio.run` works. Streaming tests run the SDK's real threads over a fake `websocket.WebSocketApp` (`tests/test_twelvedata_stream.py`). Mock vendor SDKs instead (`tests/test_yahoo.py` monkeypatches `yahoo.yf.Ticker`). To test the service layer, use `tests/fakes.py:FakeProvider`, which records calls.

## Conventions

- Keep it small: no abstractions beyond what's needed now.
- Requires Python >= 3.10, so ruff targets py310 and mypy uses python_version 3.10. The local interpreter is 3.14.
- Make small, well-described git commits per milestone.

## Remaining work

GitHub issues are the task tracker (`gh issue list`). Don't keep a task list in this file.
