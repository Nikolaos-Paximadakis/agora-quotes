# agora-quotes

One consistent interface for stock market quotes: Greek stocks (Athens Exchange, ATHEX) first, plus US and other markets. It is a thin wrapper over existing data sources (currently yfinance). Your apps depend on `agora_quotes` only, never on a vendor SDK, so a data source can be swapped by writing one adapter.

## Install

From another uv project on this machine:

```bash
uv add --editable ../agora-quotes
```

For development in this repo:

```bash
uv sync          # creates .venv and installs the package in editable mode with dev tools
```

## Usage

```python
import agora_quotes as aq

q = aq.get_quote("EXAE.AT")      # Greek stocks: Yahoo ".AT" suffix
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
```

`get_quote` raises on failure. `get_quotes` returns a quote *or an error* for each symbol, so one bad ticker doesn't break a batch. Failures that affect the whole source still raise: `RateLimited`, `ProviderError`.

All errors subclass `aq.AgoraQuotesError`. Vendor exceptions never leak out; the original exception is kept as `__cause__`.

## Configuration

These are environment variables only; see `.env.example`.

| Variable | Meaning |
|---|---|
| `AGORA_QUOTES_PROVIDER` | Default provider: `yahoo` (default), `twelvedata`, `eodhd` |
| `TWELVEDATA_API_KEY`, `EODHD_API_KEY` | API keys for those providers (not implemented yet) |

You can also set the provider in code with `aq.configure("yahoo")`, which takes a provider name or a provider instance.

## Data delays

Free data, especially for ATHEX, is usually **delayed by about 15 minutes**. Every `Quote` has:

- `delayed`. This is `True` unless the source explicitly reports the data as real-time. With Yahoo, ATHEX quotes report a 15-minute delay; US quotes report 0 and are marked `delayed=False`.
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

1. Create `src/agora_quotes/providers/<name>.py` with a class that has a `name` attribute and the `get_quote`, `get_quotes` and `get_history` methods of `providers/base.py:Provider`. Add `stream` (an async generator) if the source can push updates.
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
