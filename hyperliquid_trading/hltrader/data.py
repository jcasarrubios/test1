"""Market data: historical candles from the Hyperliquid Info API, CSV cache and synthetic data."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Protocol

import numpy as np
import pandas as pd
import requests

MAINNET_API_URL = "https://api.hyperliquid.xyz"
TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"

INTERVAL_MS = {
    "1m": 60_000,
    "3m": 3 * 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 3_600_000,
    "2h": 2 * 3_600_000,
    "4h": 4 * 3_600_000,
    "8h": 8 * 3_600_000,
    "12h": 12 * 3_600_000,
    "1d": 86_400_000,
    "3d": 3 * 86_400_000,
    "1w": 7 * 86_400_000,
}

# Hyperliquid returns at most 5000 candles per candleSnapshot request.
MAX_CANDLES_PER_REQUEST = 5000

OHLCV = ["open", "high", "low", "close", "volume"]


def interval_to_ms(interval: str) -> int:
    try:
        return INTERVAL_MS[interval]
    except KeyError as e:
        raise ValueError(f"Intervalo no soportado: {interval}. Opciones: {list(INTERVAL_MS)}") from e


def bars_per_year(interval: str) -> float:
    return 365 * 86_400_000 / interval_to_ms(interval)


def index_ms(index: pd.DatetimeIndex):
    """Epoch milliseconds of a DatetimeIndex, independent of its internal resolution."""
    return index.as_unit("ms").asi8


class CandleSource(Protocol):
    def fetch_recent(self, coin: str, interval: str, n_bars: int) -> pd.DataFrame: ...

    def mid_price(self, coin: str) -> float: ...


def candles_to_frame(raw: list[dict]) -> pd.DataFrame:
    """Convert candleSnapshot rows ({t,T,o,h,l,c,v,n,...}) to an OHLCV DataFrame indexed by open time (UTC)."""
    if not raw:
        return pd.DataFrame(columns=OHLCV, index=pd.DatetimeIndex([], tz="UTC", name="time"))
    df = pd.DataFrame(raw)
    out = pd.DataFrame(
        {
            "open": df["o"].astype(float),
            "high": df["h"].astype(float),
            "low": df["l"].astype(float),
            "close": df["c"].astype(float),
            "volume": df["v"].astype(float),
        }
    )
    out.index = pd.DatetimeIndex(pd.to_datetime(df["t"].astype("int64"), unit="ms", utc=True)).as_unit("ms")
    out.index.name = "time"
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


class HyperliquidData:
    """Thin client over POST /info. No signing required."""

    def __init__(self, base_url: str = MAINNET_API_URL, timeout: float = 15.0, session: Optional[requests.Session] = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _post(self, payload: dict):
        for attempt in range(4):
            try:
                r = self.session.post(f"{self.base_url}/info", json=payload, timeout=self.timeout)
                if r.status_code == 429:
                    raise requests.HTTPError("rate limited", response=r)
                r.raise_for_status()
                return r.json()
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)

    def candles(self, coin: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        step = interval_to_ms(interval)
        frames = []
        cursor = start_ms
        while cursor <= end_ms:
            chunk_end = min(end_ms, cursor + step * MAX_CANDLES_PER_REQUEST)
            raw = self._post(
                {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": cursor, "endTime": chunk_end}}
            )
            if raw:
                frames.append(candles_to_frame(raw))
                cursor = max(cursor + step, int(raw[-1]["t"]) + step)
            else:
                cursor = chunk_end + 1
        if not frames:
            return candles_to_frame([])
        df = pd.concat(frames)
        return df[~df.index.duplicated(keep="last")].sort_index()

    def fetch_recent(self, coin: str, interval: str, n_bars: int) -> pd.DataFrame:
        end = int(time.time() * 1000)
        start = end - interval_to_ms(interval) * (n_bars + 1)
        return self.candles(coin, interval, start, end)

    def all_mids(self) -> dict[str, float]:
        return {k: float(v) for k, v in self._post({"type": "allMids"}).items()}

    def mid_price(self, coin: str) -> float:
        return self.all_mids()[coin]

    def funding_history(self, coin: str, start_ms: int, end_ms: Optional[int] = None) -> pd.Series:
        payload = {"type": "fundingHistory", "coin": coin, "startTime": start_ms}
        if end_ms is not None:
            payload["endTime"] = end_ms
        raw = self._post(payload) or []
        s = pd.Series(
            [float(r["fundingRate"]) for r in raw],
            index=pd.to_datetime([int(r["time"]) for r in raw], unit="ms", utc=True),
            name="funding_rate",
        )
        return s.sort_index()


def drop_unclosed(df: pd.DataFrame, interval: str, now_ms: Optional[int] = None) -> pd.DataFrame:
    """Remove the candle that is still forming (open_time + interval > now)."""
    if df.empty:
        return df
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    close_ms = index_ms(df.index) + interval_to_ms(interval)
    return df[close_ms <= now_ms]


def cache_path(data_dir: str | Path, coin: str, interval: str) -> Path:
    return Path(data_dir) / f"{coin.replace('/', '_')}_{interval}.csv"


def save_csv(df: pd.DataFrame, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)


def load_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).as_unit("ms")
    df.index.name = "time"
    missing = [c for c in OHLCV if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas en {path}: {missing}")
    return df[OHLCV].astype(float).sort_index()


def load_or_fetch(
    coin: str,
    interval: str,
    days: float,
    data_dir: str | Path = "data",
    client: Optional[HyperliquidData] = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Load cached candles and fetch whatever is missing from the API."""
    client = client or HyperliquidData()
    path = cache_path(data_dir, coin, interval)
    end = int(time.time() * 1000)
    start = end - int(days * 86_400_000)
    cached = load_csv(path) if path.exists() and not refresh else None
    if cached is not None and not cached.empty:
        first, last = (int(x) for x in index_ms(cached.index[[0, -1]]))
        parts = [cached]
        if start < first:
            parts.append(client.candles(coin, interval, start, first))
        parts.append(client.candles(coin, interval, last, end))
        df = pd.concat(parts)
        df = df[~df.index.duplicated(keep="last")].sort_index()
    else:
        df = client.candles(coin, interval, start, end)
    df = drop_unclosed(df, interval, end)
    save_csv(df, path)
    return df[df.index >= pd.Timestamp(start, unit="ms", tz="UTC")]


def synthetic_candles(
    n: int = 2000,
    interval: str = "1h",
    start_price: float = 30_000.0,
    drift: float = 0.0,
    vol: float = 0.01,
    seed: int = 42,
    regime_len: int = 200,
) -> pd.DataFrame:
    """Random-walk candles with alternating trend regimes, useful for tests and offline experiments."""
    rng = np.random.default_rng(seed)
    regimes = np.repeat(rng.normal(drift, vol / 15, size=n // regime_len + 1), regime_len)[:n]
    rets = regimes + rng.normal(0, vol, size=n)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]])
    wick = np.abs(rng.normal(0, vol / 2, size=n))
    high = np.maximum(open_, close) * (1 + wick)
    low = np.minimum(open_, close) * (1 - wick)
    volume = rng.lognormal(3, 0.5, size=n)
    idx = pd.date_range("2024-01-01", periods=n, freq=pd.Timedelta(milliseconds=interval_to_ms(interval)), tz="UTC", name="time")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


class ReplaySource:
    """CandleSource that replays a DataFrame bar by bar (paper-trading dry runs and tests)."""

    def __init__(self, df: pd.DataFrame, start: int = 100):
        self.df = df
        self.pos = start

    def advance(self) -> bool:
        if self.pos >= len(self.df):
            return False
        self.pos += 1
        return True

    def fetch_recent(self, coin: str, interval: str, n_bars: int) -> pd.DataFrame:
        return self.df.iloc[max(0, self.pos - n_bars) : self.pos]

    def mid_price(self, coin: str) -> float:
        return float(self.df["close"].iloc[self.pos - 1])
