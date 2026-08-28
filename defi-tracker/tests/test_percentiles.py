from datetime import datetime, timedelta, timezone

from dft.metrics import percentiles as P


def test_percentile_rank_basic():
    assert P.percentile_rank([1, 2, 3, 4], 3) == 62.5


def test_percentile_rank_ties_use_midrank():
    assert P.percentile_rank([0, 0, 0, 0], 0) == 50.0


def test_percentile_rank_empty():
    assert P.percentile_rank([], 1) is None


def test_trailing_percentile_requires_min_obs():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    series = [(now - timedelta(days=i), float(i)) for i in range(10)]
    assert P.trailing_percentile(series, 5.0, now, lookback_days=365, min_obs=30) is None


def test_change_pct_and_value_at():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    series = sorted((now - timedelta(days=i), 100.0 - i) for i in range(30))
    assert P.value_at(series, now - timedelta(days=7)) == 93.0
    assert round(P.change_pct(series, 7, now), 4) == round(100.0 * (100 - 93) / 93, 4)


def test_value_at_outside_tolerance_returns_none():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    series = [(now - timedelta(days=10), 1.0)]
    assert P.value_at(series, now, tolerance_days=2.0) is None


def test_cross_sectional_ranks():
    ranks = P.cross_sectional_ranks({"a": 1, "b": 2, "c": 3})
    assert ranks["c"] > ranks["b"] > ranks["a"]
    assert round(ranks["b"], 2) == 50.0
