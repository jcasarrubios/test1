"""CoinGecko markets endpoint — price, market cap, FDV, supply.

The Demo tier (free key, header `x-cg-demo-api-key`) allows ~10k calls/month at
100/min, which is far more than an hourly poll of a few hundred assets needs.
Without a key the public endpoint still answers, just with tighter limits.
"""

from __future__ import annotations

import os
from typing import Any

BASE = "https://api.coingecko.com/api/v3"


def _headers() -> dict[str, str]:
    key = os.environ.get("COINGECKO_API_KEY")
    return {"x-cg-demo-api-key": key} if key else {}


def markets(http, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Market data keyed by CoinGecko id. Chunked to stay inside URL limits."""
    out: dict[str, dict[str, Any]] = {}
    for chunk in (ids[i:i + 100] for i in range(0, len(ids), 100)):
        payload = http.get_json(
            f"{BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ",".join(chunk),
                "per_page": len(chunk),
                "page": 1,
                "sparkline": "false",
            },
            headers=_headers(),
        )
        for row in payload or []:
            out[row["id"]] = row
    return out


def price_rows(http, assets: list[dict[str, Any]], ts: str) -> list[dict[str, Any]]:
    """`price_snapshots` rows for assets that carry a `coingecko_id`."""
    ids = [a["coingecko_id"] for a in assets if a.get("coingecko_id")]
    if not ids:
        return []
    data = markets(http, ids)
    rows = []
    for asset in assets:
        row = data.get(asset.get("coingecko_id") or "")
        if not row:
            continue
        rows.append({
            "asset_id": asset["id"],
            "ts": ts,
            "source": "coingecko",
            "price_usd": row.get("current_price"),
            "market_cap_usd": row.get("market_cap"),
            "fdv_usd": row.get("fully_diluted_valuation"),
            "circulating_supply": row.get("circulating_supply"),
            "total_supply": row.get("total_supply") or row.get("max_supply"),
            "volume_24h_usd": row.get("total_volume"),
        })
    return rows
