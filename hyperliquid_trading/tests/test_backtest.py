import numpy as np
import pandas as pd
import pytest

from hltrader.backtest import BacktestConfig, _Book, run_backtest
from hltrader.strategy import Strategy
from hltrader.optimize import grid_search, walk_forward, expand_grid
from hltrader import get_strategy
from .conftest import make_df


class Fixed(Strategy):
    name = ""
    defaults = {}

    def __init__(self, values):
        super().__init__()
        self.values = values

    def generate(self, df):
        return pd.Series(self.values, index=df.index, dtype=float)


NOCOST = dict(taker_fee=0.0, slippage_bps=0.0)


def test_flat_strategy_keeps_equity():
    df = make_df(np.linspace(100, 200, 50))
    res = run_backtest(Fixed([0.0] * 50), df, BacktestConfig(**NOCOST))
    assert res.equity.eq(10_000).all()
    assert res.metrics["n_trades"] == 0


def test_always_long_tracks_price_after_entry():
    closes = [100, 110, 121, 110, 132]
    df = make_df(closes)
    res = run_backtest(Fixed([1.0] * 5), df, BacktestConfig(**NOCOST))
    # signal at bar 0 is executed at the open of bar 1 (=100), so equity follows close/100
    expected = [10_000] + [10_000 * c / 100 for c in closes[1:]]
    np.testing.assert_allclose(res.equity.to_numpy(), expected)


def test_signal_executes_next_bar_open():
    df = make_df([100, 100, 100, 120, 120])
    sig = [0, 0, 1, 1, 1]  # decided at close of bar 2 -> filled at open of bar 3 (=100)
    res = run_backtest(Fixed(sig), df, BacktestConfig(**NOCOST))
    t = res.trades.iloc[0]
    assert t["entry_time"] == df.index[3] and t["entry_price"] == 100
    assert res.equity.iloc[-1] == pytest.approx(12_000)


def test_short_profits_when_price_falls():
    df = make_df([100, 100, 90, 80])
    res = run_backtest(Fixed([-1, -1, -1, -1]), df, BacktestConfig(**NOCOST))
    assert res.equity.iloc[-1] == pytest.approx(12_000)


def test_fees_and_slippage_reduce_equity():
    df = make_df([100] * 6)
    sig = [1, 0, 1, 0, 1, 0]
    res = run_backtest(Fixed(sig), df, BacktestConfig(taker_fee=0.001, slippage_bps=0))
    assert res.metrics["fees_paid"] > 0
    assert res.equity.iloc[-1] == pytest.approx(10_000 - res.metrics["fees_paid"])
    res2 = run_backtest(Fixed(sig), df, BacktestConfig(taker_fee=0, slippage_bps=10))
    assert res2.equity.iloc[-1] < 10_000


def test_stop_loss_exits_and_waits_for_new_signal():
    df = make_df([100, 100, 100, 95, 90, 120, 130])
    res = run_backtest(Fixed([1] * 7), df, BacktestConfig(stop_loss=0.03, **NOCOST))
    t = res.trades.iloc[0]
    assert t["exit_reason"] == "stop_loss"
    assert t["exit_price"] == pytest.approx(97)
    assert len(res.trades) == 1  # does not re-enter while the signal is still +1
    assert res.equity.iloc[-1] == pytest.approx(9_700)


def test_take_profit():
    df = make_df([100, 100, 104, 110, 90])
    res = run_backtest(Fixed([1] * 5), df, BacktestConfig(take_profit=0.05, **NOCOST))
    assert res.trades.iloc[0]["exit_reason"] == "take_profit"
    assert res.equity.iloc[-1] == pytest.approx(10_500)


def test_leverage_scales_pnl():
    df = make_df([100, 100, 110])
    res = run_backtest(Fixed([1, 1, 1]), df, BacktestConfig(leverage=3, **NOCOST))
    assert res.equity.iloc[-1] == pytest.approx(13_000)


def test_funding_charged_to_longs():
    df = make_df([100] * 11)
    res = run_backtest(Fixed([1] * 11), df, BacktestConfig(funding_rate_hourly=0.0001, **NOCOST))
    assert res.metrics["funding_paid"] == pytest.approx(10_000 * 0.0001 * 10)
    short = run_backtest(Fixed([-1] * 11), df, BacktestConfig(funding_rate_hourly=0.0001, **NOCOST))
    assert short.equity.iloc[-1] > 10_000


def test_book_flip_accounting():
    b = _Book(fee=0.0)
    b.fill(1.0, 100, "t0", "x")
    b.fill(-3.0, 110, "t1", "x")  # close long (+10) and open 2 short @110
    assert b.realized == pytest.approx(10)
    assert b.qty == pytest.approx(-2) and b.avg == pytest.approx(110)
    b.fill(2.0, 100, "t2", "x")
    assert b.realized == pytest.approx(30)
    assert [t["pnl"] for t in b.trades] == [pytest.approx(10), pytest.approx(20)]


def test_real_strategy_metrics(candles):
    res = run_backtest(get_strategy("ema_cross")(), candles, BacktestConfig())
    m = res.metrics
    assert m["n_trades"] > 5 and -1 <= m["max_drawdown"] <= 0
    assert res.trades["net_pnl"].sum() == pytest.approx(res.equity.iloc[-1] - 10_000, rel=1e-6, abs=1e-6)
    assert "Sharpe" in res.summary()


def test_grid_search_and_walk_forward(candles):
    cls = get_strategy("ema_cross")
    grid = {"fast": [5, 10, 60], "slow": [30, 50]}
    assert len(expand_grid(grid)) == 6
    ranked = grid_search(cls, candles, grid, metric="sharpe")
    assert len(ranked) == 4  # fast=60 combos are invalid and skipped
    assert ranked["sharpe"].is_monotonic_decreasing
    wf = walk_forward(cls, candles, grid, n_splits=3)
    assert len(wf) == 3 and {"oos_return", "fast", "slow"} <= set(wf.columns)
