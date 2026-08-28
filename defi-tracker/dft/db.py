"""SQLite access layer. Thin on purpose: rows in, dicts out."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """ISO8601 in UTC, second precision, no microseconds."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def iso_hour(dt: datetime) -> str:
    """Snapshot timestamps are truncated to the hour so re-runs collapse."""
    return iso(dt.replace(minute=0, second=0, microsecond=0))


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()


def upsert_asset(conn: sqlite3.Connection, asset: dict[str, Any]) -> int:
    """Insert or update a watchlist asset, keyed on symbol. Returns asset id."""
    cols = (
        "symbol", "name", "coingecko_id", "coinpaprika_id", "llama_slug",
        "binance_symbol", "binance_perp", "category", "is_benchmark", "watchlist",
    )
    defaults = {"is_benchmark": 0, "watchlist": 1}
    values = [asset.get(c, defaults.get(c)) if asset.get(c) is not None else defaults.get(c)
              for c in cols]
    conn.execute(
        f"""INSERT INTO assets ({", ".join(cols)}, created_at)
            VALUES ({", ".join("?" * len(cols))}, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                {", ".join(f"{c} = excluded.{c}" for c in cols[1:])}""",
        values + [iso(utcnow())],
    )
    row = conn.execute("SELECT id FROM assets WHERE symbol = ?", (asset["symbol"],)).fetchone()
    return int(row["id"])


def assets(conn: sqlite3.Connection, watchlist_only: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM assets"
    if watchlist_only:
        sql += " WHERE watchlist = 1"
    return [dict(r) for r in conn.execute(sql + " ORDER BY symbol")]


def insert_ignore(conn: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]]) -> int:
    """Insert rows, skipping ones that collide with a UNIQUE key. Returns count written."""
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0])
    sql = (
        f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(cols))})"
    )
    before = conn.total_changes
    conn.executemany(sql, [[r.get(c) for c in cols] for r in rows])
    conn.commit()
    return conn.total_changes - before


def upsert_rows(conn: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]],
                conflict: Sequence[str]) -> int:
    """Insert rows, overwriting non-key columns on conflict (re-run of the same hour)."""
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0])
    updates = [c for c in cols if c not in conflict]
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT({', '.join(conflict)}) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in updates)
    )
    conn.executemany(sql, [[r.get(c) for c in cols] for r in rows])
    conn.commit()
    return len(rows)


def query(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params)]


def one(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def latest_prices(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Most recent price snapshot per asset symbol, whichever source wrote it."""
    rows = query(conn, """
        SELECT a.symbol, p.*
          FROM price_snapshots p
          JOIN assets a ON a.id = p.asset_id
         WHERE p.ts = (SELECT MAX(ts) FROM price_snapshots WHERE asset_id = p.asset_id)
    """)
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        # Prefer the row with a market cap when several sources share a timestamp.
        prev = out.get(row["symbol"])
        if prev is None or (prev.get("market_cap_usd") is None and row.get("market_cap_usd")):
            out[row["symbol"]] = row
    return out


def latest_protocol_metrics(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return query(conn, """
        SELECT m.*
          FROM protocol_metrics m
         WHERE m.ts = (SELECT MAX(ts) FROM protocol_metrics WHERE llama_slug = m.llama_slug)
    """)


def series(conn: sqlite3.Connection, table: str, value_col: str, where: str,
           params: Sequence[Any] = ()) -> list[tuple[datetime, float]]:
    """(timestamp, value) pairs ordered oldest-first, NULLs dropped."""
    rows = conn.execute(
        f"SELECT ts, {value_col} AS v FROM {table} WHERE {where} AND {value_col} IS NOT NULL ORDER BY ts",
        params,
    )
    return [(parse_iso(r["ts"]), float(r["v"])) for r in rows]


def jdump(value: Any) -> str | None:
    return None if value is None else json.dumps(value, separators=(",", ":"))


def jload(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
