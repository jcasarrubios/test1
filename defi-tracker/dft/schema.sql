-- DeFi valuation tracker: SQLite schema.
-- Every snapshot table is append-only and uniquely keyed on (subject, ts) so a
-- re-run of the daily batch is idempotent.

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS assets (
    id                INTEGER PRIMARY KEY,
    symbol            TEXT    NOT NULL,
    name              TEXT,
    coingecko_id      TEXT,
    coinpaprika_id    TEXT,
    llama_slug        TEXT,
    binance_symbol    TEXT,
    binance_perp      TEXT,
    category          TEXT,
    is_benchmark      INTEGER NOT NULL DEFAULT 0,
    watchlist         INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT    NOT NULL,
    UNIQUE (symbol)
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id                 INTEGER PRIMARY KEY,
    asset_id           INTEGER NOT NULL REFERENCES assets(id),
    ts                 TEXT    NOT NULL,   -- ISO8601 UTC, truncated to the hour
    source             TEXT    NOT NULL,
    price_usd          REAL,
    market_cap_usd     REAL,
    fdv_usd            REAL,
    circulating_supply REAL,
    total_supply       REAL,
    volume_24h_usd     REAL,
    UNIQUE (asset_id, ts, source)
);

CREATE TABLE IF NOT EXISTS ohlcv (
    id         INTEGER PRIMARY KEY,
    asset_id   INTEGER NOT NULL REFERENCES assets(id),
    ts         TEXT    NOT NULL,          -- candle open time, ISO8601 UTC
    interval   TEXT    NOT NULL,          -- '1h', '1d'
    source     TEXT    NOT NULL,
    open       REAL, high REAL, low REAL, close REAL,
    volume     REAL,
    UNIQUE (asset_id, ts, interval, source)
);

CREATE TABLE IF NOT EXISTS protocol_metrics (
    id                  INTEGER PRIMARY KEY,
    llama_slug          TEXT    NOT NULL,
    ts                  TEXT    NOT NULL,
    asset_id            INTEGER REFERENCES assets(id),   -- NULL: protocol has no tracked token
    name                TEXT,
    category            TEXT,
    chains              TEXT,                            -- JSON array
    tvl_usd             REAL,
    fees_24h            REAL, fees_7d REAL, fees_30d REAL, fees_1y REAL,
    revenue_24h         REAL, revenue_7d REAL, revenue_30d REAL, revenue_1y REAL,
    dex_volume_24h      REAL, dex_volume_30d REAL,
    mcap_usd            REAL,
    methodology_url     TEXT,   -- read the adapter before trusting the TVL number
    UNIQUE (llama_slug, ts)
);

CREATE TABLE IF NOT EXISTS derivatives_snapshots (
    id                 INTEGER PRIMARY KEY,
    asset_id           INTEGER NOT NULL REFERENCES assets(id),
    ts                 TEXT    NOT NULL,
    source             TEXT    NOT NULL,
    funding_rate       REAL,               -- per 8h interval, as a fraction
    funding_apr        REAL,               -- funding_rate * 3 * 365
    open_interest_usd  REAL,
    UNIQUE (asset_id, ts, source)
);

-- Aggregate, non-asset-scoped series: stablecoin supply, total DeFi TVL, ...
CREATE TABLE IF NOT EXISTS aggregate_snapshots (
    id      INTEGER PRIMARY KEY,
    metric  TEXT NOT NULL,
    ts      TEXT NOT NULL,
    value   REAL,
    UNIQUE (metric, ts)
);

CREATE TABLE IF NOT EXISTS news_items (
    id               INTEGER PRIMARY KEY,
    url              TEXT NOT NULL,
    url_key          TEXT NOT NULL UNIQUE,   -- normalised URL: the hard dedupe key
    title            TEXT NOT NULL,
    title_key        TEXT NOT NULL,          -- normalised title: the fuzzy dedupe key
    source           TEXT,
    summary          TEXT,
    published_at     TEXT,
    fetched_at       TEXT NOT NULL,
    cluster_id       INTEGER,                -- id of the first item seen in this story cluster
    category         TEXT,                   -- regulatory|exploit|unlock|listing|upgrade|macro|other
    entities         TEXT,                   -- JSON array of watchlist symbols
    materiality      INTEGER,                -- 1..3; only 3s are surfaced
    rationale        TEXT,
    classifier       TEXT,                   -- model id, or 'heuristic'
    classified_at    TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY,
    ts              TEXT NOT NULL,
    signal_type     TEXT NOT NULL,
    subject         TEXT NOT NULL,          -- asset symbol, protocol slug, or 'SYSTEM'
    asset_id        INTEGER REFERENCES assets(id),
    direction       TEXT,                   -- long_lean | short_lean | context
    value           REAL,
    percentile      REAL,
    detail          TEXT,                   -- JSON
    price_at_signal REAL,
    UNIQUE (ts, signal_type, subject)
);

-- v3: every signal gets scored at fixed horizons so signal types can be
-- retired on evidence rather than on vibes.
CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id      INTEGER NOT NULL REFERENCES signals(id),
    horizon_days   INTEGER NOT NULL,
    scored_at      TEXT NOT NULL,
    price_then     REAL,
    price_now      REAL,
    return_pct     REAL,
    benchmark_pct  REAL,
    excess_pct     REAL,
    PRIMARY KEY (signal_id, horizon_days)
);

-- The pre-trade thesis rule, enforced in the data model: nothing is actionable
-- until a thesis exists, written before the fact.
CREATE TABLE IF NOT EXISTS theses (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    subject     TEXT NOT NULL,
    thesis      TEXT NOT NULL,
    invalidator TEXT,                       -- what would make this wrong
    horizon     TEXT,
    closed_at   TEXT,
    outcome     TEXT
);

CREATE INDEX IF NOT EXISTS idx_price_asset_ts    ON price_snapshots(asset_id, ts);
CREATE INDEX IF NOT EXISTS idx_ohlcv_asset_ts    ON ohlcv(asset_id, interval, ts);
CREATE INDEX IF NOT EXISTS idx_protocol_slug_ts  ON protocol_metrics(llama_slug, ts);
CREATE INDEX IF NOT EXISTS idx_deriv_asset_ts    ON derivatives_snapshots(asset_id, ts);
CREATE INDEX IF NOT EXISTS idx_agg_metric_ts     ON aggregate_snapshots(metric, ts);
CREATE INDEX IF NOT EXISTS idx_news_fetched      ON news_items(fetched_at);
CREATE INDEX IF NOT EXISTS idx_news_cluster      ON news_items(cluster_id);
CREATE INDEX IF NOT EXISTS idx_signals_ts        ON signals(ts);
CREATE INDEX IF NOT EXISTS idx_signals_type      ON signals(signal_type);
