"""Command-line entry point.

    dft init                 # create the DB and load watchlist from config
    dft ingest                # pull prices, protocol metrics, OHLCV, derivatives
    dft news                  # fetch feeds, dedupe, classify
    dft signals                # compute percentile flags from stored history
    dft digest                 # render out/digest-YYYY-MM-DD.md
    dft score-outcomes         # v3: score matured signals
    dft run                    # ingest + news + signals + score-outcomes + digest
    dft report                 # print the signal-type outcome report
    dft thesis add SUBJECT ... # log a pre-trade thesis
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from . import db, outcomes
from .config import load as load_config
from .digest import render as render_digest
from .http import Http
from .metrics import percentiles as P
from .metrics import signals as S
from .metrics.multiples import screen as screen_multiples
from .news.classify import classify
from .news.dedupe import dedupe
from .sources import binance, coingecko, coinpaprika, defillama, feeds

log = logging.getLogger("dft")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def cmd_init(cfg, args) -> None:
    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    for asset in cfg.assets:
        db.upsert_asset(conn, asset)
    print(f"initialised {cfg.db_path} with {len(cfg.assets)} watchlist assets")


def cmd_ingest(cfg, args) -> None:
    conn = db.connect(cfg.db_path)
    http = Http.from_config(cfg)
    assets = db.assets(conn)
    ts = db.iso_hour(db.utcnow())

    price_rows = coingecko.price_rows(http, assets, ts)
    got_symbols = {r["asset_id"] for r in price_rows}
    missing = [a for a in assets if a["id"] not in got_symbols and a.get("coinpaprika_id")]
    if missing:
        log.info("coingecko missing %d assets; falling back to coinpaprika", len(missing))
        price_rows += coinpaprika.price_rows(http, missing, ts)
    n = db.upsert_rows(conn, "price_snapshots", price_rows, conflict=["asset_id", "ts", "source"])
    log.info("wrote %d price snapshots", n)

    slugs = cfg.protocol_slugs
    asset_by_slug = {a["llama_slug"]: a["id"] for a in assets if a.get("llama_slug")}
    proto_rows, unmatched = defillama.protocol_metric_rows(http, slugs, asset_by_slug, ts)
    n = db.upsert_rows(conn, "protocol_metrics", proto_rows, conflict=["llama_slug", "ts"])
    log.info("wrote %d protocol metric rows (%d unmatched slugs)", n, len(unmatched))

    stable = defillama.stablecoin_history(http)
    if stable:
        agg_rows = [{"metric": "stablecoin_supply", "ts": db.iso(ts_), "value": v} for ts_, v in stable]
        n = db.upsert_rows(conn, "aggregate_snapshots", agg_rows, conflict=["metric", "ts"])
        log.info("wrote %d stablecoin_supply points", n)

    tvl_hist = defillama.total_tvl_history(http)
    if tvl_hist:
        agg_rows = [{"metric": "total_tvl", "ts": db.iso(ts_), "value": v} for ts_, v in tvl_hist]
        n = db.upsert_rows(conn, "aggregate_snapshots", agg_rows, conflict=["metric", "ts"])
        log.info("wrote %d total_tvl points", n)

    ohlcv_rows = binance.ohlcv_rows(http, assets, interval="1h", limit=200)
    n = db.upsert_rows(conn, "ohlcv", ohlcv_rows, conflict=["asset_id", "ts", "interval", "source"])
    log.info("wrote %d OHLCV candles", n)

    deriv_rows = binance.derivatives_rows(http, assets, ts)
    n = db.upsert_rows(conn, "derivatives_snapshots", deriv_rows, conflict=["asset_id", "ts", "source"])
    log.info("wrote %d derivatives snapshots", n)

    funding_hist_rows = binance.funding_history_rows(http, assets, limit=200)
    n = db.upsert_rows(conn, "derivatives_snapshots", funding_hist_rows,
                       conflict=["asset_id", "ts", "source"])
    log.info("wrote %d funding history points", n)


def cmd_news(cfg, args) -> None:
    conn = db.connect(cfg.db_path)
    http = Http.from_config(cfg)
    raw = feeds.fetch_all(http, cfg.feeds)
    clustered = dedupe(raw, threshold=cfg.get("news.title_similarity", 0.82))
    log.info("fetched %d raw items, %d after dedupe", len(raw), len(clustered))

    existing = {r["url_key"] for r in db.query(conn, "SELECT url_key FROM news_items")}
    new_items = [i for i in clustered if i["url_key"] not in existing]
    if not new_items:
        print("no new news items")
        return

    watchlist = cfg.watchlist_symbols
    model = cfg.get("news.model", "claude-opus-5")
    batch_size = int(cfg.get("news.classify_batch_size", 20))
    classified = classify(new_items, watchlist, model, batch_size)

    fetched_at = db.iso(db.utcnow())
    rows = [{
        "url": i["url"], "url_key": i["url_key"], "title": i["title"], "title_key": i["title_key"],
        "source": i["source"], "summary": i.get("summary"), "published_at": i.get("published_at"),
        "fetched_at": fetched_at, "cluster_id": None,
        "category": i.get("category"), "entities": db.jdump(i.get("entities")),
        "materiality": i.get("materiality"), "rationale": i.get("rationale"),
        "classifier": i.get("classifier"), "classified_at": fetched_at,
    } for i in classified]
    n = db.insert_ignore(conn, "news_items", rows)
    print(f"stored {n} new news items ({sum(1 for r in rows if r['materiality'] == 3)} material)")


def _protocol_tvl_series(conn) -> dict[str, list[tuple[datetime, float]]]:
    slugs = [r["llama_slug"] for r in db.query(conn, "SELECT DISTINCT llama_slug FROM protocol_metrics")]
    return {slug: db.series(conn, "protocol_metrics", "tvl_usd", "llama_slug = ?", (slug,))
            for slug in slugs}


def _funding_series(conn, assets: list[dict]) -> dict[str, list[tuple[datetime, float]]]:
    out = {}
    for asset in assets:
        rows = db.series(conn, "derivatives_snapshots", "funding_rate", "asset_id = ?", (asset["id"],))
        if rows:
            out[asset["symbol"]] = rows
    return out


def cmd_signals(cfg, args) -> None:
    conn = db.connect(cfg.db_path)
    assets = db.assets(conn)
    asset_ids = {a["symbol"]: a["id"] for a in assets}
    prices = {sym: row["price_usd"] for sym, row in db.latest_prices(conn).items()}
    now = db.utcnow()
    ts = db.iso_hour(now)
    lookback = int(cfg.get("percentiles.lookback_days", 365))
    min_obs = int(cfg.get("percentiles.min_obs", 30))

    proto_rows = db.latest_protocol_metrics(conn)
    proto_by_slug = {r["llama_slug"]: r for r in proto_rows}
    latest = db.latest_prices(conn)
    asset_rows = []
    for a in assets:
        row = dict(a)
        p = latest.get(a["symbol"])
        if p:
            row["fdv_usd"], row["market_cap_usd"] = p.get("fdv_usd"), p.get("market_cap_usd")
        asset_rows.append(row)

    mrows = screen_multiples(
        asset_rows, proto_by_slug,
        float(cfg.get("screen.min_annualised_fees_usd", 2_000_000)),
        cfg.get("screen.double_count_categories", []),
    )

    all_signals: list[S.Signal] = []
    all_signals += S.cross_sectional_multiples(
        mrows, asset_ids,
        float(cfg.get("signals.ps_cheap_percentile", 20)),
        float(cfg.get("signals.ps_expensive_percentile", 90)),
        prices, ts,
    )
    slug_to_symbol = {a["llama_slug"]: a["symbol"] for a in assets if a.get("llama_slug")}
    all_signals += S.tvl_change_flags(
        _protocol_tvl_series(conn), asset_ids, slug_to_symbol, now, lookback, min_obs,
        float(cfg.get("signals.tvl_change_percentile", 95)), prices, ts,
    )
    all_signals += S.funding_crowding_flags(
        _funding_series(conn, assets), asset_ids, now, lookback, min_obs,
        float(cfg.get("signals.funding_percentile", 95)), prices, ts,
    )
    stable_series = db.series(conn, "aggregate_snapshots", "value", "metric = 'stablecoin_supply'")
    stable_sig = S.stablecoin_supply_flag(
        stable_series, now, lookback, min_obs,
        float(cfg.get("signals.stablecoin_percentile", 90)), 7, ts,
    )
    if stable_sig:
        all_signals.append(stable_sig)

    rows = [S.to_row(s) for s in all_signals]
    n = db.upsert_rows(conn, "signals", rows, conflict=["ts", "signal_type", "subject"])
    print(f"computed {len(all_signals)} signals ({n} rows written)")


def cmd_digest(cfg, args) -> None:
    conn = db.connect(cfg.db_path)
    assets = db.assets(conn)
    prices = db.latest_prices(conn)
    proto_by_slug = {r["llama_slug"]: r for r in db.latest_protocol_metrics(conn)}

    asset_rows = []
    for a in assets:
        row = dict(a)
        p = prices.get(a["symbol"])
        if p:
            row["fdv_usd"], row["market_cap_usd"] = p.get("fdv_usd"), p.get("market_cap_usd")
        asset_rows.append(row)

    mrows = screen_multiples(
        asset_rows, proto_by_slug,
        float(cfg.get("screen.min_annualised_fees_usd", 2_000_000)),
        cfg.get("screen.double_count_categories", []),
    )

    now = db.utcnow()
    since = db.iso(now.replace(hour=0, minute=0, second=0, microsecond=0))
    todays_signals = db.query(
        conn, "SELECT * FROM signals WHERE ts >= ? ORDER BY percentile DESC", (since,)
    )
    lookback_hours = int(cfg.get("news.lookback_hours", 36))
    cutoff = db.iso(now - __import__("datetime").timedelta(hours=lookback_hours))
    news_rows = db.query(
        conn, "SELECT * FROM news_items WHERE fetched_at >= ? ORDER BY materiality DESC, fetched_at DESC",
        (cutoff,),
    )
    for r in news_rows:
        r["entities"] = db.jload(r.get("entities"), [])

    stable_series = db.series(conn, "aggregate_snapshots", "value", "metric = 'stablecoin_supply'")
    stable_note = None
    if stable_series:
        change = P.change_pct(stable_series, 7, now)
        dist = P.change_series(stable_series, 7)
        pct = P.trailing_percentile(dist, change, now, int(cfg.get("percentiles.lookback_days", 365)),
                                    int(cfg.get("percentiles.min_obs", 30))) if change is not None else None
        if change is not None:
            pct_str = f", P{pct:.0f} of trailing year" if pct is not None else ""
            stable_note = f"Aggregate stablecoin supply 7d change: {change:+.2f}%{pct_str}."

    content = render_digest(
        now, mrows, todays_signals, news_rows,
        int(cfg.get("news.surface_materiality", 3)), stable_note,
    )
    out_dir = cfg.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"digest-{now.strftime('%Y-%m-%d')}.md"
    out_path.write_text(content)
    latest_path = out_dir / "digest-latest.md"
    latest_path.write_text(content)
    print(f"wrote {out_path}")


def cmd_score_outcomes(cfg, args) -> None:
    conn = db.connect(cfg.db_path)
    n = outcomes.score_due_signals(conn, cfg.benchmark_symbol)
    print(f"scored {n} signal outcomes")


def cmd_report(cfg, args) -> None:
    conn = db.connect(cfg.db_path)
    rows = outcomes.signal_type_report(conn)
    if not rows:
        print("no scored outcomes yet")
        return
    print(f"{'signal_type':<24} {'horizon':>7} {'n':>5} {'avg_ret%':>9} {'avg_excess%':>12} {'hit%':>6}")
    for r in rows:
        print(f"{r['signal_type']:<24} {r['horizon_days']:>6}d {r['n']:>5} "
              f"{r['avg_return_pct']:>9.2f} {r['avg_excess_pct'] if r['avg_excess_pct'] is not None else float('nan'):>12.2f} "
              f"{r['hit_rate_pct'] if r['hit_rate_pct'] is not None else float('nan'):>6.1f}")


def cmd_thesis_add(cfg, args) -> None:
    conn = db.connect(cfg.db_path)
    conn.execute(
        "INSERT INTO theses (created_at, subject, thesis, invalidator, horizon) VALUES (?,?,?,?,?)",
        (db.iso(db.utcnow()), args.subject, args.thesis, args.invalidator, args.horizon),
    )
    conn.commit()
    print(f"thesis logged for {args.subject}")


def cmd_run(cfg, args) -> None:
    cmd_ingest(cfg, args)
    cmd_news(cfg, args)
    cmd_signals(cfg, args)
    cmd_score_outcomes(cfg, args)
    cmd_digest(cfg, args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dft", description="DeFi valuation tracker")
    parser.add_argument("--config", default=None, help="path to config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the DB and load the watchlist").set_defaults(func=cmd_init)
    sub.add_parser("ingest", help="pull prices/protocol metrics/OHLCV/derivatives").set_defaults(func=cmd_ingest)
    sub.add_parser("news", help="fetch, dedupe, classify news").set_defaults(func=cmd_news)
    sub.add_parser("signals", help="compute percentile-rank signals").set_defaults(func=cmd_signals)
    sub.add_parser("digest", help="render the daily digest").set_defaults(func=cmd_digest)
    sub.add_parser("score-outcomes", help="score matured signal outcomes").set_defaults(func=cmd_score_outcomes)
    sub.add_parser("report", help="print signal-type outcome report").set_defaults(func=cmd_report)
    sub.add_parser("run", help="run the full daily pipeline").set_defaults(func=cmd_run)

    thesis = sub.add_parser("thesis", help="thesis log")
    thesis_sub = thesis.add_subparsers(dest="thesis_command", required=True)
    thesis_add = thesis_sub.add_parser("add", help="log a pre-trade thesis")
    thesis_add.add_argument("subject")
    thesis_add.add_argument("thesis")
    thesis_add.add_argument("--invalidator", default=None)
    thesis_add.add_argument("--horizon", default=None)
    thesis_add.set_defaults(func=cmd_thesis_add)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    cfg = load_config(args.config)
    try:
        args.func(cfg, args)
    except Exception as exc:  # noqa: BLE001
        log.error("dft %s failed: %s", args.command, exc, exc_info=args.verbose)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
