"""Valuation multiples: the part a price ticker doesn't show.

P/F and P/S use FDV, not circulating market cap — FDV is the honest denominator
because fee/revenue capture ultimately accrues (or doesn't) against full
supply, and comparing mcap-based ratios across assets with very different
unlock schedules is misleading. Mcap/TVL is reported separately, flagged for
categories where DeFiLlama TVL is prone to double counting (liquid staking,
restaking, bridges) — read the protocol's adapter before trusting that number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Multiple:
    symbol: str
    llama_slug: str | None
    category: str | None
    fdv_usd: float | None
    mcap_usd: float | None
    tvl_usd: float | None
    annualised_fees: float | None
    annualised_revenue: float | None
    price_to_fees: float | None
    price_to_sales: float | None
    mcap_to_tvl: float | None
    tvl_double_count_risk: bool
    below_fee_floor: bool


def _annualise(fees_24h: float | None, fees_7d: float | None, fees_30d: float | None,
              fees_1y: float | None) -> float | None:
    """Prefer the longest window available — 24h fees annualised naively is noisy."""
    if fees_1y:
        return fees_1y
    if fees_30d:
        return fees_30d * (365 / 30)
    if fees_7d:
        return fees_7d * (365 / 7)
    if fees_24h:
        return fees_24h * 365
    return None


def _div(numerator: float | None, denominator: float | None) -> float | None:
    if not numerator or not denominator:
        return None
    return numerator / denominator


def compute(asset: dict[str, Any], protocol: dict[str, Any] | None,
           min_annualised_fees_usd: float, double_count_categories: set[str]) -> Multiple:
    """Build one row of the multiples screen for a watchlist asset."""
    fdv = asset.get("fdv_usd")
    mcap = asset.get("market_cap_usd")
    protocol = protocol or {}
    tvl = protocol.get("tvl_usd")
    ann_fees = _annualise(protocol.get("fees_24h"), protocol.get("fees_7d"),
                          protocol.get("fees_30d"), protocol.get("fees_1y"))
    ann_rev = _annualise(protocol.get("revenue_24h"), protocol.get("revenue_7d"),
                         protocol.get("revenue_30d"), protocol.get("revenue_1y"))
    below_floor = bool(ann_fees and ann_fees < min_annualised_fees_usd)
    category = protocol.get("category") or asset.get("category")
    return Multiple(
        symbol=asset["symbol"],
        llama_slug=protocol.get("llama_slug") or asset.get("llama_slug"),
        category=category,
        fdv_usd=fdv,
        mcap_usd=mcap,
        tvl_usd=tvl,
        annualised_fees=ann_fees,
        annualised_revenue=ann_rev,
        # Below the fee floor a P/F ratio is arithmetic noise — suppress it
        # rather than publish a multiple built on a rounding error.
        price_to_fees=None if below_floor else _div(fdv, ann_fees),
        price_to_sales=None if below_floor else _div(fdv, ann_rev),
        mcap_to_tvl=_div(mcap, tvl),
        tvl_double_count_risk=(category in double_count_categories) if category else False,
        below_fee_floor=below_floor,
    )


def screen(assets: list[dict[str, Any]], protocols_by_slug: dict[str, dict[str, Any]],
          min_annualised_fees_usd: float, double_count_categories: list[str]) -> list[Multiple]:
    dc = set(double_count_categories)
    out = []
    for asset in assets:
        slug = asset.get("llama_slug")
        out.append(compute(asset, protocols_by_slug.get(slug) if slug else None,
                           min_annualised_fees_usd, dc))
    return out


def rank_by_category(rows: list[Multiple], field: str) -> dict[str, list[Multiple]]:
    """Rows grouped by category, each group sorted cheapest-first (ascending multiple)."""
    groups: dict[str, list[Multiple]] = {}
    for row in rows:
        if getattr(row, field) is None:
            continue
        groups.setdefault(row.category or "Uncategorised", []).append(row)
    for group in groups.values():
        group.sort(key=lambda r: getattr(r, field))
    return groups
