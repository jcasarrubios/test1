"""Signal generation: cross-sectional multiples flags, time-series percentile
flags, and the funding/OI crowding gauge — each written to `signals` with a
percentile so the outcome-scoring pass (v3) can later tell which of these
signal types are actually worth anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import percentiles as P
from .multiples import Multiple


@dataclass
class Signal:
    ts: str
    signal_type: str
    subject: str
    asset_id: int | None
    direction: str          # long_lean | short_lean | context
    value: float | None
    percentile: float | None
    detail: dict[str, Any]
    price_at_signal: float | None


def cross_sectional_multiples(rows: list[Multiple], asset_ids: dict[str, int],
                              cheap_pct: float, expensive_pct: float,
                              prices: dict[str, float], ts: str) -> list[Signal]:
    """P/S rank within category: flag the cheap and expensive tails.

    Cheap does not mean "buy" — it means priced low relative to peers on this
    one ratio. Direction is a lean for the digest to caveat, not a call.
    """
    out: list[Signal] = []
    by_category: dict[str, dict[str, float]] = {}
    for row in rows:
        if row.price_to_sales is None:
            continue
        by_category.setdefault(row.category or "Uncategorised", {})[row.symbol] = row.price_to_sales

    for category, values in by_category.items():
        if len(values) < 3:
            continue  # a rank against fewer than 3 peers isn't a cross-section
        ranks = P.cross_sectional_ranks(values)
        for symbol, pct in ranks.items():
            if pct <= cheap_pct:
                direction = "long_lean"
            elif pct >= expensive_pct:
                direction = "short_lean"
            else:
                continue
            out.append(Signal(
                ts=ts, signal_type="ps_cross_sectional", subject=symbol,
                asset_id=asset_ids.get(symbol), direction=direction,
                value=values[symbol], percentile=pct,
                detail={"category": category, "peer_count": len(values)},
                price_at_signal=prices.get(symbol),
            ))
    return out


def tvl_change_flags(protocol_tvl_series: dict[str, list[tuple[datetime, float]]],
                     asset_ids: dict[str, int], slug_to_symbol: dict[str, str],
                     now: datetime, lookback_days: int, min_obs: int,
                     flag_percentile: float, prices: dict[str, float], ts: str,
                     window_days: int = 7) -> list[Signal]:
    """7d TVL change in the extreme tail of its own trailing-year distribution."""
    out: list[Signal] = []
    for slug, series in protocol_tvl_series.items():
        symbol = slug_to_symbol.get(slug)
        if not symbol or len(series) < min_obs:
            continue
        change = P.change_pct(series, window_days, now)
        if change is None:
            continue
        change_dist = P.change_series(series, window_days)
        pct = P.trailing_percentile(change_dist, change, now, lookback_days, min_obs)
        if pct is None:
            continue
        if pct >= flag_percentile:
            direction = "context"
        elif pct <= 100 - flag_percentile:
            direction = "context"
        else:
            continue
        out.append(Signal(
            ts=ts, signal_type="tvl_change_percentile", subject=symbol,
            asset_id=asset_ids.get(symbol), direction=direction,
            value=change, percentile=pct,
            detail={"llama_slug": slug, "window_days": window_days},
            price_at_signal=prices.get(symbol),
        ))
    return out


def funding_crowding_flags(funding_series: dict[str, list[tuple[datetime, float]]],
                           asset_ids: dict[str, int], now: datetime,
                           lookback_days: int, min_obs: int, flag_percentile: float,
                           prices: dict[str, float], ts: str) -> list[Signal]:
    """Funding rate at a percentile extreme: a crowding gauge, never a standalone call."""
    out: list[Signal] = []
    for symbol, series in funding_series.items():
        if len(series) < min_obs or not series:
            continue
        latest = series[-1][1]
        pct = P.trailing_percentile(series, latest, now, lookback_days, min_obs)
        if pct is None:
            continue
        if pct >= flag_percentile or pct <= 100 - flag_percentile:
            out.append(Signal(
                ts=ts, signal_type="funding_crowding", subject=symbol,
                asset_id=asset_ids.get(symbol), direction="context",
                value=latest, percentile=pct, detail={"note": "crowding gauge, not a call"},
                price_at_signal=prices.get(symbol),
            ))
    return out


def stablecoin_supply_flag(series: list[tuple[datetime, float]], now: datetime,
                           lookback_days: int, min_obs: int, flag_percentile: float,
                           window_days: int, ts: str) -> Signal | None:
    """Aggregate stablecoin supply momentum — a system-liquidity proxy."""
    if len(series) < min_obs:
        return None
    change = P.change_pct(series, window_days, now)
    if change is None:
        return None
    change_dist = P.change_series(series, window_days)
    pct = P.trailing_percentile(change_dist, change, now, lookback_days, min_obs)
    if pct is None or (pct < flag_percentile and pct > 100 - flag_percentile):
        return None
    return Signal(
        ts=ts, signal_type="stablecoin_supply", subject="SYSTEM", asset_id=None,
        direction="context", value=change, percentile=pct,
        detail={"window_days": window_days}, price_at_signal=None,
    )


def to_row(sig: Signal) -> dict[str, Any]:
    import json
    return {
        "ts": sig.ts, "signal_type": sig.signal_type, "subject": sig.subject,
        "asset_id": sig.asset_id, "direction": sig.direction, "value": sig.value,
        "percentile": sig.percentile,
        "detail": json.dumps(sig.detail, separators=(",", ":")),
        "price_at_signal": sig.price_at_signal,
    }
