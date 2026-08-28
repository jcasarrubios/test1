"""v3: score every fired signal's forward return at fixed horizons.

The point of this table is empirical, not decorative: after enough history,
`signal_type` performance can be queried directly instead of guessed at. A
signal is scored once per horizon, against both its own asset's forward return
and the configured benchmark's, so "did this beat just holding BTC" is a
one-line query.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from .db import iso, parse_iso, query, series, upsert_rows, utcnow

log = logging.getLogger(__name__)

HORIZONS_DAYS = (7, 30)


def _price_near(conn, asset_id: int, when: datetime, tolerance_days: float = 2.0) -> float | None:
    rows = series(conn, "price_snapshots", "price_usd", "asset_id = ?", (asset_id,))
    if not rows:
        return None
    from .metrics.percentiles import value_at
    return value_at(rows, when, tolerance_days)


def score_due_signals(conn, benchmark_symbol: str) -> int:
    """Score every signal whose horizon has matured and hasn't been scored yet."""
    benchmark = query(conn, "SELECT id FROM assets WHERE symbol = ?", (benchmark_symbol,))
    benchmark_id = benchmark[0]["id"] if benchmark else None

    now = utcnow()
    signals = query(conn, "SELECT * FROM signals WHERE asset_id IS NOT NULL")
    already = {(r["signal_id"], r["horizon_days"])
               for r in query(conn, "SELECT signal_id, horizon_days FROM signal_outcomes")}

    rows = []
    for sig in signals:
        sig_ts = parse_iso(sig["ts"])
        for horizon in HORIZONS_DAYS:
            if (sig["id"], horizon) in already:
                continue
            due_at = sig_ts + timedelta(days=horizon)
            if due_at > now:
                continue
            price_then = sig["price_at_signal"] or _price_near(conn, sig["asset_id"], sig_ts)
            price_now = _price_near(conn, sig["asset_id"], due_at)
            if not price_then or not price_now:
                continue
            ret = 100.0 * (price_now - price_then) / price_then
            bench_ret = None
            if benchmark_id:
                b_then = _price_near(conn, benchmark_id, sig_ts)
                b_now = _price_near(conn, benchmark_id, due_at)
                if b_then and b_now:
                    bench_ret = 100.0 * (b_now - b_then) / b_then
            rows.append({
                "signal_id": sig["id"], "horizon_days": horizon, "scored_at": iso(now),
                "price_then": price_then, "price_now": price_now, "return_pct": ret,
                "benchmark_pct": bench_ret,
                "excess_pct": (ret - bench_ret) if bench_ret is not None else None,
            })
    if rows:
        upsert_rows(conn, "signal_outcomes", rows, conflict=["signal_id", "horizon_days"])
    return len(rows)


def signal_type_report(conn) -> list[dict[str, Any]]:
    """Mean/median excess return per (signal_type, horizon) — the noise audit."""
    rows = query(conn, """
        SELECT s.signal_type, o.horizon_days,
               COUNT(*) AS n,
               AVG(o.return_pct) AS avg_return_pct,
               AVG(o.excess_pct) AS avg_excess_pct,
               SUM(CASE WHEN o.excess_pct > 0 THEN 1 ELSE 0 END) AS n_positive
          FROM signal_outcomes o
          JOIN signals s ON s.id = o.signal_id
         GROUP BY s.signal_type, o.horizon_days
         ORDER BY s.signal_type, o.horizon_days
    """)
    for row in rows:
        row["hit_rate_pct"] = (100.0 * row["n_positive"] / row["n"]) if row["n"] else None
    return rows
