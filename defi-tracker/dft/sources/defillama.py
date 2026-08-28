"""DeFiLlama: TVL, fees, revenue, DEX volume, stablecoin supply.

Keyless and the anchor source for everything on the fundamentals side. The
fee/revenue endpoints are the ones that matter here — they are what make a
price-to-fees multiple computable at all.

Endpoint reference: https://api-docs.defillama.com (free tier).
Token unlocks and bridge flows sit behind the paid tier and are out of scope.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..db import iso_hour, utcnow

log = logging.getLogger(__name__)

BASE = "https://api.llama.fi"
STABLE = "https://stablecoins.llama.fi"


# --- raw endpoint wrappers ---------------------------------------------------

def protocols(http) -> list[dict[str, Any]]:
    """All protocols with current TVL, category, chains, mcap."""
    return http.get_json(f"{BASE}/protocols")


def _overview(http, kind: str, data_type: str) -> dict[str, dict[str, Any]]:
    """`/overview/{kind}` keyed by protocol slug.

    kind: 'fees' | 'dexs'; data_type: 'dailyFees' | 'dailyRevenue' | 'dailyVolume'.
    """
    payload = http.get_json(
        f"{BASE}/overview/{kind}",
        params={
            "excludeTotalDataChart": "true",
            "excludeTotalDataChartBreakdown": "true",
            "dataType": data_type,
        },
    )
    out: dict[str, dict[str, Any]] = {}
    for row in payload.get("protocols", []) or []:
        slug = row.get("slug") or _slugify(row.get("name", ""))
        if slug:
            out[slug] = row
    return out


def fees(http) -> dict[str, dict[str, Any]]:
    return _overview(http, "fees", "dailyFees")


def revenue(http) -> dict[str, dict[str, Any]]:
    return _overview(http, "fees", "dailyRevenue")


def dex_volume(http) -> dict[str, dict[str, Any]]:
    return _overview(http, "dexs", "dailyVolume")


def stablecoin_history(http) -> list[tuple[datetime, float]]:
    """Aggregate stablecoin circulating supply, daily, full history."""
    payload = http.get_json(f"{STABLE}/stablecoincharts/all")
    out = []
    for row in payload or []:
        total = row.get("totalCirculatingUSD") or row.get("totalCirculating") or {}
        value = total.get("peggedUSD") if isinstance(total, dict) else None
        if value is None or not row.get("date"):
            continue
        out.append((datetime.fromtimestamp(int(row["date"]), tz=timezone.utc), float(value)))
    return sorted(out)


def total_tvl_history(http) -> list[tuple[datetime, float]]:
    """Total DeFi TVL across all chains, daily, full history."""
    payload = http.get_json(f"{BASE}/v2/historicalChainTvl")
    return sorted(
        (datetime.fromtimestamp(int(r["date"]), tz=timezone.utc), float(r["tvl"]))
        for r in payload or [] if r.get("date") and r.get("tvl") is not None
    )


# --- assembly ----------------------------------------------------------------

def _slugify(name: str) -> str:
    return "-".join(name.lower().split())


def _num(row: dict[str, Any] | None, key: str) -> float | None:
    if not row:
        return None
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _match(table: dict[str, dict[str, Any]], slug: str,
           by_name: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Overview slugs occasionally differ from /protocols slugs; fall back to name."""
    if slug in table:
        return table[slug]
    return by_name.get(slug.replace("-", " "))


def protocol_metric_rows(http, slugs: list[str], asset_by_slug: dict[str, int] | None = None,
                         ts: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Build `protocol_metrics` rows for `slugs`.

    Returns (rows, unmatched_slugs). Unmatched slugs are reported rather than
    silently dropped — a typo in the config otherwise looks like a protocol
    that simply earns no fees.
    """
    ts = ts or iso_hour(utcnow())
    asset_by_slug = asset_by_slug or {}

    tvl_rows = {p.get("slug"): p for p in protocols(http) if p.get("slug")}
    fee_rows, rev_rows, vol_rows = fees(http), revenue(http), dex_volume(http)
    fee_by_name = {(r.get("name") or "").lower(): r for r in fee_rows.values()}
    rev_by_name = {(r.get("name") or "").lower(): r for r in rev_rows.values()}
    vol_by_name = {(r.get("name") or "").lower(): r for r in vol_rows.values()}

    rows, unmatched = [], []
    for slug in slugs:
        tvl = tvl_rows.get(slug)
        fee = _match(fee_rows, slug, fee_by_name)
        rev = _match(rev_rows, slug, rev_by_name)
        vol = _match(vol_rows, slug, vol_by_name)
        if tvl is None and fee is None and rev is None:
            unmatched.append(slug)
            continue
        meta = tvl or fee or rev or {}
        rows.append({
            "llama_slug": slug,
            "ts": ts,
            "asset_id": asset_by_slug.get(slug),
            "name": meta.get("name"),
            "category": meta.get("category"),
            "chains": _chains(meta),
            "tvl_usd": _num(tvl, "tvl"),
            "fees_24h": _num(fee, "total24h"),
            "fees_7d": _num(fee, "total7d"),
            "fees_30d": _num(fee, "total30d"),
            "fees_1y": _num(fee, "total1y"),
            "revenue_24h": _num(rev, "total24h"),
            "revenue_7d": _num(rev, "total7d"),
            "revenue_30d": _num(rev, "total30d"),
            "revenue_1y": _num(rev, "total1y"),
            "dex_volume_24h": _num(vol, "total24h"),
            "dex_volume_30d": _num(vol, "total30d"),
            "mcap_usd": _num(tvl, "mcap"),
            # Methodology link: read the adapter before trusting a TVL number.
            "methodology_url": (fee or {}).get("methodologyURL")
                               or f"https://defillama.com/protocol/{slug}",
        })
    if unmatched:
        log.warning("DeFiLlama: no data for slugs %s", ", ".join(unmatched))
    return rows, unmatched


def _chains(meta: dict[str, Any]) -> str | None:
    import json
    chains = meta.get("chains")
    return json.dumps(chains, separators=(",", ":")) if isinstance(chains, list) else None
