import pandas as pd
import pytest

from hltrader.data import (
    HyperliquidData, candles_to_frame, drop_unclosed, load_csv, load_or_fetch, save_csv, synthetic_candles,
)

H = 3_600_000


def raw(t):
    return {"t": t, "T": t + H - 1, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "10", "n": 3, "i": "1h", "s": "BTC"}


class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeSession:
    """Serves hourly candles from a fixed range, at most `cap` per request (like the real API)."""

    def __init__(self, first, last, cap=3):
        self.first, self.last, self.cap, self.calls = first, last, cap, []

    def post(self, url, json, timeout):
        self.calls.append(json)
        req = json["req"]
        ts = [t for t in range(self.first, self.last + 1, H) if req["startTime"] <= t <= req["endTime"]]
        return FakeResp([raw(t) for t in ts[: self.cap]])


def test_candles_to_frame():
    df = candles_to_frame([raw(0), raw(H), raw(H)])
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2 and df["close"].iloc[0] == 1.5
    assert str(df.index.tz) == "UTC"


def test_pagination_collects_all_candles(monkeypatch):
    monkeypatch.setattr("hltrader.data.MAX_CANDLES_PER_REQUEST", 3)
    sess = FakeSession(0, 9 * H, cap=3)
    df = HyperliquidData(session=sess).candles("BTC", "1h", 0, 9 * H)
    assert len(df) == 10
    assert df.index.is_monotonic_increasing and not df.index.duplicated().any()
    assert len(sess.calls) >= 4


def test_drop_unclosed():
    df = candles_to_frame([raw(0), raw(H), raw(2 * H)])
    assert len(drop_unclosed(df, "1h", now_ms=2 * H + 10)) == 2
    assert len(drop_unclosed(df, "1h", now_ms=3 * H)) == 3


def test_csv_roundtrip(tmp_path):
    df = synthetic_candles(50)
    save_csv(df, tmp_path / "x.csv")
    back = load_csv(tmp_path / "x.csv")
    pd.testing.assert_frame_equal(back, df, check_freq=False, check_index_type=False)


def test_load_or_fetch_uses_cache(tmp_path, monkeypatch):
    now = 1_000 * H
    monkeypatch.setattr("hltrader.data.time.time", lambda: now / 1000)
    sess = FakeSession(0, now, cap=10_000)
    client = HyperliquidData(session=sess)
    df = load_or_fetch("BTC", "1h", days=1, data_dir=tmp_path, client=client)
    assert len(df) == 24  # the candle opening at `now` is still forming and dropped
    n_calls = len(sess.calls)
    df2 = load_or_fetch("BTC", "1h", days=1, data_dir=tmp_path, client=client)
    pd.testing.assert_frame_equal(df, df2)
    # the second call only asks for the tail after the cached data
    assert sess.calls[n_calls]["req"]["startTime"] == df.index.as_unit("ms").asi8[-1]


def test_bad_interval():
    with pytest.raises(ValueError):
        synthetic_candles(10, "7m")
