import pandas as pd
import pytest

from hltrader.data import synthetic_candles


@pytest.fixture
def candles() -> pd.DataFrame:
    return synthetic_candles(1500, "1h", seed=7)


def make_df(closes, interval="1h"):
    """Candles where open == previous close and high/low bracket open/close exactly."""
    closes = list(map(float, closes))
    opens = [closes[0]] + closes[:-1]
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="1h", tz="UTC", name="time")
    return pd.DataFrame(
        {"open": opens, "high": [max(o, c) for o, c in zip(opens, closes)],
         "low": [min(o, c) for o, c in zip(opens, closes)], "close": closes, "volume": 1.0},
        index=idx,
    )
