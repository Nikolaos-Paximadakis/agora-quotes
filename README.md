# agora-quotes

One consistent interface for stock market quotes: Greek stocks (Athens Exchange, ATHEX) first, plus US and other markets. It is a thin wrapper over existing data sources (yfinance, Twelve Data, EODHD). Your apps depend on `agora_quotes` only, never on a vendor SDK, so a data source can be swapped by writing one adapter.

## Install

From another uv project on this machine:

```bash
uv add --editable ../agora-quotes
uv add --editable '../agora-quotes[twelvedata]'   # also install the Twelve Data client
```

For development in this repo:

```bash
uv sync          # creates .venv and installs the package in editable mode with dev tools
```

## Usage

```python
import agora_quotes as aq

q = aq.get_quote("EXAE.AT")      # same as "ATHEX:EXAE"
q.symbol                          # 'ATHEX:EXAE' (canonical form)
q.price, q.currency               # 10.22, 'EUR'
q.timestamp                       # market time of the price (UTC)
q.retrieved_at                    # when agora_quotes fetched it (UTC)
q.delayed, q.source               # True, 'yahoo'

results = aq.get_quotes(["AAPL", "EXAE.AT", "NOPE.AT"])
for symbol, value in results.items():
    if isinstance(value, aq.AgoraQuotesError):
        print(symbol, "failed:", value)   # e.g. SymbolNotFound, returned inline
    else:
        print(symbol, value.price)

bars = aq.get_history("AAPL", start="2026-01-01", interval="1d")
bars[0].timestamp, bars[0].close  # bar start (UTC), unadjusted close
```

### Symbols

Each of these forms is accepted:

| Input | Parsed as | Canonical (`Quote.symbol`) |
|---|---|---|
| `"ATHEX:EXAE"`, `"NASDAQ:AAPL"` | exchange-qualified | `ATHEX:EXAE`, `NASDAQ:AAPL` |
| `"EXAE.AT"`, `"VOD.L"`, `"SAP.DE"` | Yahoo-style suffix | `ATHEX:EXAE`, `LSE:VOD`, `XETRA:SAP` |
| `"AAPL"` | plain ticker, provider default (US) | `AAPL` |

The known exchanges are ATHEX, NASDAQ, NYSE, LSE and XETRA (`symbols.YAHOO_SUFFIXES`). An unknown exchange prefix raises `InvalidSymbol`. An unknown suffix such as `BRK.B` is treated as part of a plain ticker.

### Quotes

`get_quote` raises on failure. `get_quotes` returns a quote *or an error* for each symbol, so one bad ticker doesn't break a batch. Failures that affect the whole source still raise: `RateLimited`, `ProviderError`. If a provider hits its rate limit partway through a batch (Twelve Data fetches one symbol per request, EODHD 15), the quotes already fetched are kept and the remaining symbols get `RateLimited` inline.

### History

`get_history(symbol, start, end=None, interval="1d")` returns a `list[Bar]` from `start` up to, but not including, `end`. `end` defaults to now.

- **Arguments.** `start` and `end` take an ISO string, a `date` or a timezone-aware `datetime`. For intraday bars a date means midnight UTC. Intervals: `1m`, `5m`, `15m`, `1h`, `1d`, `1wk`, `1mo`.
- **Bounds.** Intraday bars satisfy `start <= timestamp < end`. Daily, weekly and monthly bars are picked by their **trading date on the exchange**: dates from `start`'s date up to, but not including, `end`'s date. A datetime's date is taken in its own timezone, so `datetime(2026, 1, 6, tzinfo=ZoneInfo("Europe/Athens"))` means 6 January; an `end` after midnight includes that day. So `start="2018-08-27", end="2018-08-28"` is the 27 August session on any exchange, even though an Athens bar starts at Athens midnight, which is `2018-08-26T21:00Z`. A weekly or monthly bar counts when its week or month overlaps the range, so `start="2026-01-15", interval="1mo"` starts with the January bar.
- **Prices** are **unadjusted** for splits and dividends: each bar is the price that actually traded that day, so it can be multiplied by the quantity held that day. Yahoo serves split-adjusted closes even with dividend adjustment off, so the Yahoo adapter reads the symbol's split history (one extra request) and undoes every split dated after each bar; if that history can't be read, the call raises `ProviderError` rather than return adjusted prices. Volume is un-adjusted the same way. Because Yahoo stores prices as float32, a bar from before a large reverse split carries only the digits Yahoo kept.
- **Suspensions.** Yahoo can fill a trading suspension with flat zero-volume bars repeating a stale price, and occasionally serves an outlier; check `volume` before trusting a single bar.
- **Results.** A range with no trading returns `[]`; an unknown symbol raises `SymbolNotFound`.
- **Intraday limits.** Yahoo serves intraday data only for recent periods: `1m` for 30 days, `5m`/`15m` for 60 days, `1h` for 730 days. Older requests raise `ProviderError`.
- **Twelve Data** returns at most 5000 bars per request. A range that reaches that cap raises `ProviderError` rather than silently returning a truncated list; ask for a shorter range.
- **EODHD** serves at most 120 days of `1m` bars and 600 days of `5m`/`15m` bars per request (7200 days of `1h`). Longer ranges raise `ProviderError` before any request is made. The free plan serves one year of daily history; a range reaching further back raises `ProviderError` instead of returning only its recent part (or `[]`), so a fallback provider can answer it. Weekly and monthly bars start on the first trading day of the week or month. EODHD's free plan has no intraday data, so intraday requests on it raise `ProviderError`.

### Streaming

`aq.stream(symbols)` is an async generator that yields a `Quote` every time a price changes. It needs a provider that can stream: Yahoo (the default, no key needed) or Twelve Data (`agora-quotes[twelvedata]`).

```python
import asyncio
import agora_quotes as aq

async def watch() -> None:
    async for q in aq.stream(["AAPL", "ATHEX:EXAE"]):
        print(q.symbol, q.price, q.timestamp)

task = asyncio.create_task(watch())
...
task.cancel()   # disconnects the websocket
```

- **Which provider.** The stream uses the first configured provider that can stream: the primary, then the fallback. Yahoo and Twelve Data both can, so `provider="yahoo", fallback="twelvedata"` streams from Yahoo; use `provider="twelvedata"` to stream from Twelve Data. If the stream fails, it does not fall over to another provider.
- **Stopping.** Cancel the consuming task to disconnect. If you `break` out of the loop instead, wrap the generator in `contextlib.aclosing(...)` so it closes right away rather than when it is garbage-collected.
- **Errors.** A connection that can't be restored after 5 reconnect attempts raises `ProviderError` from the loop, as does a failed first connection. Brief drops are reconnected and resubscribed automatically. With Twelve Data, a rejected API key or a refused symbol also raises `ProviderError`. With Yahoo, an unknown symbol raises `SymbolNotFound` before connecting.
- **Quotes.** Streamed quotes are not cached. Twelve Data's are always `delayed=True`, the same as its REST quotes. Your Twelve Data plan decides which markets you can stream; check that ATHEX is covered before relying on it.
- **Yahoo.** Yahoo's websocket never says how delayed it is, so the stream first fetches each symbol's quote info (one request per symbol) and takes `delayed` from it, the same as `get_quote`: ATHEX is `delayed=True` (15 minutes), US is `delayed=False`. Only regular-session updates are yielded; pre- and post-market prices are skipped, matching `get_quote`. While a market is closed, nothing arrives.

### Errors

All errors subclass `aq.AgoraQuotesError`. Vendor exceptions never leak out; the original exception is kept as `__cause__`.

## Configuration

These are environment variables only; see `.env.example`.

| Variable | Meaning |
|---|---|
| `AGORA_QUOTES_PROVIDER` | Default provider: `yahoo` (default), `twelvedata`, `eodhd` |
| `AGORA_QUOTES_FALLBACK` | Optional second provider, tried when the first fails |
| `AGORA_QUOTES_CACHE_TTL` | Quote cache lifetime in seconds (default 60, `0` disables) |
| `AGORA_QUOTES_CACHE_PATH` | SQLite file for a quote cache shared between apps (default: unset, in-memory per process) |
| `TWELVEDATA_API_KEY` | Twelve Data API key; required when `twelvedata` is configured (missing → `ConfigurationError`) |
| `EODHD_API_KEY` | EODHD API key; required when `eodhd` is configured (missing → `ConfigurationError`) |

Or configure it in code:

```python
aq.configure(provider="yahoo", fallback="eodhd", cache_ttl=30)
```

Arguments left as `None` are read from the environment; `fallback=False` turns off a fallback that `AGORA_QUOTES_FALLBACK` sets. Providers can be given as names or instances, and reconfiguring empties an in-memory cache (a shared SQLite cache keeps its entries).

**Fallback.** When the primary provider raises, the fallback is tried:

- `get_quotes` retries only the symbols that failed.
- `SymbolNotFound` is reported only when every provider says so; otherwise you get the first real failure, such as `RateLimited`.

**Cache.** Only quotes are cached, keyed by canonical symbol, so `EXAE.AT` and `ATHEX:EXAE` share an entry. By default the cache lives **inside one process**, so separate apps don't share it. Set `AGORA_QUOTES_CACHE_PATH` to the same file (e.g. `~/.cache/agora-quotes/quotes.db`) in each app to share one SQLite cache between them; it needs no server. Each app applies its own TTL to the quotes it writes. If the file can't be created, the first call raises `ConfigurationError`; later database errors are logged and count as a cache miss. Keep the file on a local disk, since SQLite locking is unreliable on network filesystems.

## Data delays

Free data, especially for ATHEX, is usually **delayed by about 15 minutes**. Every `Quote` has:

- `delayed`. This is `True` unless the source explicitly reports the data as real-time. With Yahoo, ATHEX quotes report a 15-minute delay; US quotes report 0 and are marked `delayed=False`. Twelve Data never says whether a quote is real-time, so its quotes are always `delayed=True`. Neither does EODHD, whose live prices are documented as 15–20 minutes delayed, so its quotes are always `delayed=True` too. EODHD doesn't report a currency; it is filled in for ATHEX, US and XETRA and left `None` for LSE.
- `timestamp`, the market time the price refers to. Outside trading hours this is the last close, so check it before treating a price as current.
- `source` and `retrieved_at`.

Never show a delayed quote to a user as "live".

## Licensing and redistribution

Market data isn't free to redistribute.

- **Yahoo Finance / yfinance**: yfinance is an unofficial client, not affiliated with Yahoo. Yahoo's terms allow personal use only. Don't redistribute the data or use it commercially.
- **ATHEX data**: redistributing Athens Exchange prices, even delayed, generally requires a licence from the exchange.
- **Twelve Data, EODHD**: what you may do depends on your subscription plan.

Check the current terms before showing quotes to anyone other than yourself.

## Adding a provider

1. Create `src/agora_quotes/providers/<name>.py` with a class that has a `name` attribute and the `get_quote`, `get_quotes` and `get_history` methods of `providers/base.py:Provider`. Methods receive parsed `Symbol` objects; convert them to the vendor's format (see `yahoo_symbol`) and set `Quote.symbol`/`Bar.symbol` to `str(symbol)`. Add `stream` (an async generator, see `providers/base.py:StreamingProvider`) if the source can push updates; `aq.stream` picks it up automatically.
2. Convert vendor responses into `Quote`/`Bar` with UTC timestamps. Set `delayed=False` only when the source says the data is real-time.
3. Catch vendor exceptions and re-raise them as `SymbolNotFound`, `RateLimited` or `ProviderError` (`raise ... from e`).
4. Read any API key from an environment variable and add it to `.env.example`.
5. Register it in `registry.PROVIDERS`. Import the vendor SDK inside your module only, never at the package top level.
6. Add tests that mock the vendor SDK.

## Development

```bash
uv run pytest                                   # no network: sockets are blocked in tests
AGORA_QUOTES_LIVE_TESTS=1 uv run pytest -m live # real calls to the data sources
uv run ruff check src tests && uv run ruff format src tests
uv run mypy                                     # strict, over src/
```
