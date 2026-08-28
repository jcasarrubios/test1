"""Binance public endpoints — OHLCV, funding rates, open interest.

Direct rather than via an aggregator: free, generous limits, no aggregator lag,
and real hourly history (which no free aggregator tier gives you). Funding and
OI are read as a crowding gauge, not as a signal in their own right.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..db import iso

log = logging.getLogger(__name__)

SPOT = "https://api.binance.com"
FUTURES = "https://fapi.binance.com"


def _ts(ms: int) -> str:
    return iso(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))


def klines(http, symbol: str, interval: str = "1h", limit: int = 1000,
           start_ms: int | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)}
    if start_ms:
        params["startTime"] = start_ms
    payload = http.get_json(f"{SPOT}/api/v3/klines", params=params)
    return [
        {
            "ts": _ts(int(k[0])), "interval": interval, "source": "binance",
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
            "close": float(k[4]), "volume": float(k[5]),
        }
        for k in payload or []
    ]


def ohlcv_rows(http, assets: list[dict[str, Any]], interval: str = "1h",
               limit: int = 1000) -> list[dict[str, Any]]:
    """Backfill candles for every asset with a `binance_symbol`."""
    rows = []
    for asset in assets:
        symbol = asset.get("binance_symbol")
        if not symbol:
            continue
        try:
            for candle in klines(http, symbol, interval=interval, limit=limit):
                rows.append({"asset_id": asset["id"], **candle})
        except Exception as exc:  # noqa: BLE001
            log.warning("binance klines failed for %s: %s", symbol, exc)
    return rows


def funding_and_oi(http, symbol: str) -> dict[str, Any]:
    """Current funding rate and USD open interest for a USDT-margined perp."""
    premium = http.get_json(f"{FUTURES}/fapi/v1/premiumIndex", params={"symbol": symbol})
    oi = http.get_json(f"{FUTURES}/fapi/v1/openInterest", params={"symbol": symbol})
    rate = float(premium.get("lastFundingRate", 0.0))
    mark = float(premium.get("markPrice", 0.0) or 0.0)
    contracts = float(oi.get("openInterest", 0.0) or 0.0)
    return {
        "funding_rate": rate,
        "funding_apr": rate * 3 * 365,   # funding settles every 8h
        "open_interest_usd": contracts * mark if mark else None,
    }


def derivatives_rows(http, assets: list[dict[str, Any]], ts: str) -> list[dict[str, Any]]:
    rows = []
    for asset in assets:
        symbol = asset.get("binance_perp")
        if not symbol:
            continue
        try:
            rows.append({"asset_id": asset["id"], "ts": ts, "source": "binance",
                         **funding_and_oi(http, symbol)})
        except Exception as exc:  # noqa: BLE001
            log.warning("binance derivatives failed for %s: %s", symbol, exc)
    return rows


def funding_history(http, symbol: str, limit: int = 1000) -> list[dict[str, Any]]:
    """Historical funding settlements — the trailing distribution a percentile needs."""
    payload = http.get_json(f"{FUTURES}/fapi/v1/fundingRate",
                            params={"symbol": symbol, "limit": min(limit, 1000)})
    out = []
    for row in payload or []:
        rate = float(row["fundingRate"])
        out.append({
            "ts": _ts(int(row["fundingTime"])), "source": "binance",
            "funding_rate": rate, "funding_apr": rate * 3 * 365,
            "open_interest_usd": None,
        })
    return out


def funding_history_rows(http, assets: list[dict[str, Any]], limit: int = 1000) -> list[dict[str, Any]]:
    rows = []
    for asset in assets:
        symbol = asset.get("binance_perp")
        if not symbol:
            continue
        try:
            for row in funding_history(http, symbol, limit=limit):
                rows.append({"asset_id": asset["id"], **row})
        except Exception as exc:  # noqa: BLE001
            log.warning("binance funding history failed for %s: %s", symbol, exc)
    return rows
