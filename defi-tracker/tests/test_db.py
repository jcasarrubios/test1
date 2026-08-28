import pytest

from dft import db


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    yield c
    c.close()


def test_upsert_asset_is_idempotent(conn):
    id1 = db.upsert_asset(conn, {"symbol": "ETH", "name": "Ethereum"})
    id2 = db.upsert_asset(conn, {"symbol": "ETH", "name": "Ethereum (renamed)"})
    assert id1 == id2
    row = db.one(conn, "SELECT * FROM assets WHERE id = ?", (id1,))
    assert row["name"] == "Ethereum (renamed)"


def test_upsert_asset_defaults(conn):
    aid = db.upsert_asset(conn, {"symbol": "BTC"})
    row = db.one(conn, "SELECT * FROM assets WHERE id = ?", (aid,))
    assert row["watchlist"] == 1
    assert row["is_benchmark"] == 0


def test_insert_ignore_skips_conflicts(conn):
    aid = db.upsert_asset(conn, {"symbol": "ETH"})
    rows = [{"asset_id": aid, "ts": "2026-01-01T00:00:00+00:00", "source": "t", "price_usd": 1.0}]
    assert db.insert_ignore(conn, "price_snapshots", rows) == 1
    assert db.insert_ignore(conn, "price_snapshots", rows) == 0


def test_upsert_rows_overwrites_on_conflict(conn):
    aid = db.upsert_asset(conn, {"symbol": "ETH"})
    row = {"asset_id": aid, "ts": "2026-01-01T00:00:00+00:00", "source": "t", "price_usd": 1.0}
    db.upsert_rows(conn, "price_snapshots", [row], conflict=["asset_id", "ts", "source"])
    row["price_usd"] = 2.0
    db.upsert_rows(conn, "price_snapshots", [row], conflict=["asset_id", "ts", "source"])
    got = db.one(conn, "SELECT price_usd FROM price_snapshots WHERE asset_id = ?", (aid,))
    assert got["price_usd"] == 2.0


def test_iso_hour_truncates():
    from datetime import datetime, timezone
    dt = datetime(2026, 1, 1, 10, 37, 42, tzinfo=timezone.utc)
    assert db.iso_hour(dt) == "2026-01-01T10:00:00+00:00"


def test_jdump_jload_roundtrip():
    data = {"a": [1, 2, 3]}
    assert db.jload(db.jdump(data)) == data
    assert db.jload(None, default=[]) == []
    assert db.jload("not json", default="fallback") == "fallback"
