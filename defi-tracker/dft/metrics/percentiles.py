"""Percentile ranks and windowed changes.

Raw values do not travel: "TVL up 12%" carries no information without knowing
what 12% is for this protocol. Everything the digest surfaces is therefore
expressed as a rank against the asset's own trailing distribution, or against
its category cross-section.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from typing import Sequence

Series = Sequence[tuple[datetime, float]]


def percentile_rank(values: Sequence[float], value: float) -> float | None:
    """Midrank percentile of `value` within `values`, 0-100.

    Midrank (counting half of the ties) keeps a flat series — e.g. a funding
    rate pinned at zero — from reporting as either the 0th or the 100th
    percentile depending on comparison direction.
    """
    if not values:
        return None
    ordered = sorted(values)
    below = bisect_left(ordered, value)
    equal = bisect_right(ordered, value) - below
    return 100.0 * (below + equal / 2) / len(ordered)


def window(series: Series, now: datetime, lookback_days: int) -> list[float]:
    cutoff = now - timedelta(days=lookback_days)
    return [v for ts, v in series if cutoff <= ts <= now]


def trailing_percentile(series: Series, value: float, now: datetime,
                        lookback_days: int = 365, min_obs: int = 30) -> float | None:
    """Percentile of `value` in the trailing window. None when history is too thin."""
    values = window(series, now, lookback_days)
    if len(values) < min_obs:
        return None
    return percentile_rank(values, value)


def value_at(series: Series, when: datetime, tolerance_days: float = 2.0) -> float | None:
    """Last observation at or before `when`, if one exists within `tolerance_days`."""
    idx = bisect_right(series, (when, float("inf"))) - 1
    if idx < 0:
        return None
    ts, value = series[idx]
    if (when - ts) > timedelta(days=tolerance_days):
        return None
    return value


def change_pct(series: Series, days: int, now: datetime | None = None,
               tolerance_days: float = 2.0) -> float | None:
    """Percent change over `days`, measured from the latest observation."""
    if not series:
        return None
    now = now or series[-1][0]
    latest = series[-1][1]
    then = value_at(series, now - timedelta(days=days), tolerance_days)
    if then in (None, 0):
        return None
    return 100.0 * (latest - then) / abs(then)


def change_series(series: Series, days: int, tolerance_days: float = 2.0) -> list[tuple[datetime, float]]:
    """Rolling `days`-change series — the distribution a change-percentile ranks against."""
    out = []
    for ts, value in series:
        then = value_at(series, ts - timedelta(days=days), tolerance_days)
        if then not in (None, 0):
            out.append((ts, 100.0 * (value - then) / abs(then)))
    return out


def cross_sectional_ranks(values: dict[str, float]) -> dict[str, float]:
    """Percentile rank of each key's value within the group. 100 = highest."""
    pool = list(values.values())
    return {k: percentile_rank(pool, v) for k, v in values.items()}
