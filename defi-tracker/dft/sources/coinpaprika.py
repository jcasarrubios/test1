"""CoinPaprika — keyless fallback for price and market cap.

Used when CoinGecko errors or rate-limits, so one throttled source never costs
a day of history. Paprika's free tier caps historical granularity at 24h, which
is exactly why this project snapshots into its own store from day one.
"""

from __future__ import annotations

from typing import Any

BASE = "https://api.coinpaprika.com/v1"


def tickers(http, ids: list[str]) -> dict[str, dict[str, Any]]:
    """One call per id; Paprika's bulk endpoint returns thousands of coins."""
    out: dict[str, dict[str, Any]] = {}
    for coin_id in ids:
        try:
            out[coin_id] = http.get_json(f"{BASE}/tickers/{coin_id}", params={"quotes": "USD"})
        except Exception:  # noqa: BLE001 - a missing id must not abort the run
            continue
    return out


def price_rows(http, assets: list[dict[str, Any]], ts: str) -> list[dict[str, Any]]:
    ids = [a["coinpaprika_id"] for a in assets if a.get("coinpaprika_id")]
    if not ids:
        return []
    data = tickers(http, ids)
    rows = []
    for asset in assets:
        row = data.get(asset.get("coinpaprika_id") or "")
        if not row:
            continue
        quote = (row.get("quotes") or {}).get("USD") or {}
        total_supply = row.get("total_supply") or row.get("max_supply")
        price = quote.get("price")
        rows.append({
            "asset_id": asset["id"],
            "ts": ts,
            "source": "coinpaprika",
            "price_usd": price,
            "market_cap_usd": quote.get("market_cap"),
            # Paprika has no FDV field; derive it when supply and price are known.
            "fdv_usd": (price * total_supply) if price and total_supply else None,
            "circulating_supply": row.get("circulating_supply"),
            "total_supply": total_supply,
            "volume_24h_usd": quote.get("volume_24h"),
        })
    return rows
