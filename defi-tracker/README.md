# DeFi Valuation Tracker

A price tracker is a solved problem. This is not that — it's a fundamentals
screen (price-to-fees, price-to-sales, mcap/TVL) ranked cross-sectionally and
against each asset's own trailing-year distribution, plus a news pipeline that
turns an RSS firehose into a once-a-day digest filtered down to what's actually
material. It runs on free, keyless (or generous-free-tier) APIs, keeps its own
price/OHLCV history from day one, and logs every signal it fires so you can
score, three months in, which signal types are noise and which aren't.

Design principle: **this is a scheduled batch job, not a push-alert feed.**
Given a pre-trade-thesis discipline, real-time alerts are the machine that
manufactures reasons to enter reactively. The digest fires once at a fixed
hour, is read once, and nothing in it is actionable until a thesis is logged
for it first (`dft thesis add`).

## What it computes

- **P/F and P/S** — FDV over annualised fees / revenue, from DeFiLlama's
  fees/revenue endpoints. Ratios below a configurable minimum-fee floor are
  suppressed rather than published as noise.
- **Mcap/TVL** — reported with a same-category double-count flag (liquid
  staking, restaking, bridges, CDPs are the categories where DeFiLlama TVL is
  most often inflated). Read the protocol's adapter link before trusting a
  number, always.
- **Cross-sectional P/S rank** — percentile within category; the cheap/expensive
  tails become signals, explicitly labelled a lean, not a call.
- **Trailing-year percentile flags** — TVL 7d change, funding-rate crowding,
  aggregate stablecoin-supply momentum. "TVL up 12%" means nothing on its own;
  "TVL 7d change at the 95th percentile of its own trailing year" means
  something.
- **News materiality** — RSS from Cointelegraph, The Block, Decrypt, CoinDesk,
  The Defiant, CryptoPanic, deduped (exact URL + fuzzy title match — the same
  wire story hits eight outlets), classified into
  regulatory/exploit/unlock_emission/listing/upgrade/macro/other with a 1–3
  materiality score via the Claude API. Only 3s reach the digest. Degrades to
  a keyword heuristic with no API key configured, so the pipeline never
  breaks for lack of a key.
- **Signal outcomes (v3)** — every signal is scored against its own asset's
  forward return and the benchmark's, at 7d and 30d, once the horizon matures.
  `dft report` prints mean/median excess return and hit rate per signal type —
  the empirical answer to which of these is worth looking at.

## Data sources

| Data | Source | Auth |
|---|---|---|
| TVL, fees, revenue, DEX volume, stablecoin supply | [DeFiLlama](https://defillama.com) (`api.llama.fi`) | none |
| Price, market cap, FDV | [CoinGecko](https://coingecko.com) demo tier | optional (`COINGECKO_API_KEY`) |
| Price fallback | [CoinPaprika](https://coinpaprika.com) | none |
| OHLCV, funding rate, open interest | Binance spot/futures public endpoints | none |
| News | RSS feeds (configurable) | none |
| News classification | Claude API | `ANTHROPIC_API_KEY` (optional — falls back to a keyword heuristic) |

Token unlocks and bridge flows sit behind DeFiLlama's paid tier and are
intentionally out of scope for now.

## Setup

```bash
cd defi-tracker
pip install -r requirements-dev.txt   # or requirements.txt without tests
cp config/config.example.toml config/config.toml   # edit the watchlist/feeds
export ANTHROPIC_API_KEY=...     # optional — enables real news classification
export COINGECKO_API_KEY=...     # optional — raises the CoinGecko rate limit

python -m dft.cli init            # create data/dft.db, load the watchlist
python -m dft.cli run             # ingest + news + signals + score + digest
cat out/digest-latest.md
```

Or install as a package (`pip install -e .`) to get the `dft` command directly.

## Commands

```
dft init                              create the DB, load the watchlist from config
dft ingest                            pull prices, protocol metrics, OHLCV, derivatives
dft news                              fetch feeds, dedupe, classify
dft signals                           compute percentile-rank signals from stored history
dft digest                            render out/digest-YYYY-MM-DD.md (+ digest-latest.md)
dft score-outcomes                    score matured signals against forward returns
dft report                            print the per-signal-type outcome table
dft run                               the above, in order
dft thesis add SUBJECT "..." \
    [--invalidator "..."] [--horizon "..."]   log a pre-trade thesis
```

## Scheduling

`.github/workflows/daily-digest.yml` runs `dft run` once a day, caching
`data/dft.db` between runs and committing the digest + database back to the
repo. Set `ANTHROPIC_API_KEY` (and optionally `COINGECKO_API_KEY`) as repo
secrets to enable real news classification; without it the run still
completes using the heuristic classifier.

For local scheduling, cron or `launchd` calling `dft run` works the same way —
the pipeline is idempotent (every write is keyed so a re-run collapses).

## Project layout

```
dft/
  db.py              SQLite access layer (schema.sql defines 10 tables)
  config.py           TOML config loading
  http.py             shared HTTP client: per-host throttle, retry/backoff
  sources/            defillama.py, coingecko.py, coinpaprika.py, binance.py, feeds.py
  metrics/
    percentiles.py     percentile ranks, windowed % change — the "how rare is this" layer
    multiples.py        P/F, P/S, mcap/TVL
    signals.py           turns metrics into flagged, logged signals
  news/
    dedupe.py            URL + fuzzy title dedupe
    classify.py           Claude-based classification, keyword fallback
  digest.py             Markdown digest renderer
  outcomes.py            v3 signal outcome scoring
  cli.py                 `dft` entry point
tests/                  pytest — percentiles, multiples, dedupe, feeds, db, classify (32 tests)
config/config.example.toml   default watchlist (12 assets) + feed list + thresholds
```

## Build order (as shipped)

- **v0** — SQLite schema + ingestion CLI. No UI. *(this)*
- **v1** — the Markdown digest: ranked screen, percentile flags, filtered news. *(this)*
- **v2** — a dashboard (Streamlit or similar), once real usage shows which
  views actually get opened. Not built yet — premature before v0/v1 have run
  long enough to know.
- **v3** — signal outcome scoring. *(this, `dft report`)* — needs a few months
  of accumulated `signal_outcomes` before the report is more than noise
  itself; the scoring pipeline is what's shipped now, the empirical answer
  comes with time.

## Caveats

- **TVL is a contested metric.** DeFiLlama documents methodology per protocol
  via adapter source links (surfaced as `methodology_url` on each protocol
  metrics row); double-counting is flagged by category but not corrected for.
- **P/F and P/S use FDV**, not circulating market cap, so ratios are
  comparable across assets with different unlock schedules — but that also
  means a token with a large unvested supply will screen "expensive" on a
  denominator that isn't fully liquid yet. Read alongside `mcap_usd`.
- **Funding rate and OI are a crowding gauge, never a standalone signal** —
  every funding-based row in `signals` is tagged `direction: "context"` for
  exactly this reason.
- **CoinPaprika's own free tier caps hourly/OHLCV history at 24h** — which is
  why this project snapshots into its own database from the first run. The
  earlier you start it, the more useful the trailing-percentile signals get.
