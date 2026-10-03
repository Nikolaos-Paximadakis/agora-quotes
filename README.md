# agora-quotes

One consistent interface for stock market quotes: Greek stocks (Athens Exchange, ATHEX) first, plus US and other markets. It is a thin wrapper over existing data sources (yfinance, Twelve Data). Your apps depend on `agora_quotes` only, never on a vendor SDK, so a data source can be swapped by writing one adapter.

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

`get_quote` raises on failure. `get_quotes` returns a quote *or an error* for each symbol, so one bad ticker doesn't break a batch. Failures that affect the whole source still raise: `RateLimited`, `ProviderError`. If a provider hits its rate limit partway through a batch (Twelve Data fetches one symbol per request), the quotes already fetched are kept and the remaining symbols get `RateLimited` inline.

### History

`get_history(symbol, start, end=None, interval="1d")` returns a `list[Bar]` with `start <= timestamp < end`. `end` defaults to now.

- **Arguments.** `start` and `end` take an ISO string, a `date` (both mean midnight UTC) or a timezone-aware `datetime`. Intervals: `1m`, `5m`, `15m`, `1h`, `1d`, `1wk`, `1mo`.
- **Prices** are **unadjusted** for splits and dividends.
- **Results.** A range with no trading returns `[]`; an unknown symbol raises `SymbolNotFound`.
- **Intraday limits.** Yahoo serves intraday data only for recent periods: `1m` for 30 days, `5m`/`15m` for 60 days, `1h` for 730 days. Older requests raise `ProviderError`.
- **Twelve Data** returns at most 5000 bars per request. A range that reaches that cap raises `ProviderError` rather than silently returning a truncated list; ask for a shorter range.

### Errors

All errors subclass `aq.AgoraQuotesError`. Vendor exceptions never leak out; the original exception is kept as `__cause__`.

## Configuration

These are environment variables only; see `.env.example`.

| Variable | Meaning |
|---|---|
| `AGORA_QUOTES_PROVIDER` | Default provider: `yahoo` (default), `twelvedata`, `eodhd` |
| `AGORA_QUOTES_FALLBACK` | Optional second provider, tried when the first fails |
| `AGORA_QUOTES_CACHE_TTL` | Quote cache lifetime in seconds (default 60, `0` disables) |
| `TWELVEDATA_API_KEY` | Twelve Data API key; required when `twelvedata` is configured (missing → `ConfigurationError`) |
| `EODHD_API_KEY` | EODHD API key (provider not implemented yet) |

Or configure it in code:

```python
aq.configure(provider="yahoo", fallback="eodhd", cache_ttl=30)
```

Arguments left as `None` are read from the environment. Providers can be given as names or instances, and reconfiguring empties the cache.

**Fallback.** When the primary provider raises, the fallback is tried:

- `get_quotes` retries only the symbols that failed.
- `SymbolNotFound` is reported only when every provider says so; otherwise you get the first real failure, such as `RateLimited`.

**Cache.** Only quotes are cached, keyed by canonical symbol, so `EXAE.AT` and `ATHEX:EXAE` share an entry. The cache lives **inside one process**: separate apps don't share it.

## Data delays

Free data, especially for ATHEX, is usually **delayed by about 15 minutes**. Every `Quote` has:

- `delayed`. This is `True` unless the source explicitly reports the data as real-time. With Yahoo, ATHEX quotes report a 15-minute delay; US quotes report 0 and are marked `delayed=False`. Twelve Data never says whether a quote is real-time, so its quotes are always `delayed=True`.
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

1. Create `src/agora_quotes/providers/<name>.py` with a class that has a `name` attribute and the `get_quote`, `get_quotes` and `get_history` methods of `providers/base.py:Provider`. Methods receive parsed `Symbol` objects; convert them to the vendor's format (see `yahoo_symbol`) and set `Quote.symbol`/`Bar.symbol` to `str(symbol)`. Add `stream` (an async generator) if the source can push updates.
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
