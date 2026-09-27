"""Parameter search: grid search and walk-forward (in-sample optimisation, out-of-sample validation)."""
from __future__ import annotations

import itertools
from typing import Any, Optional

import pandas as pd

from .backtest import BacktestConfig, run_backtest
from .strategy import Strategy

HIGHER_IS_BETTER = {"sharpe", "sortino", "calmar", "total_return", "cagr", "profit_factor", "win_rate", "final_equity"}


def expand_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


def grid_search(
    strategy_cls: type[Strategy],
    df: pd.DataFrame,
    grid: dict[str, list[Any]],
    config: Optional[BacktestConfig] = None,
    metric: str = "sharpe",
    fixed: Optional[dict[str, Any]] = None,
    min_trades: int = 1,
) -> pd.DataFrame:
    rows = []
    for params in expand_grid(grid):
        full = {**(fixed or {}), **params}
        try:
            strat = strategy_cls(**full)
        except ValueError:
            continue  # invalid combination (e.g. fast >= slow)
        res = run_backtest(strat, df, config)
        rows.append({**params, **{k: res.metrics[k] for k in
                     ("sharpe", "sortino", "total_return", "cagr", "max_drawdown", "calmar", "n_trades", "win_rate", "profit_factor")}})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out[out["n_trades"] >= min_trades]
    return out.sort_values(metric, ascending=metric not in HIGHER_IS_BETTER).reset_index(drop=True)


def walk_forward(
    strategy_cls: type[Strategy],
    df: pd.DataFrame,
    grid: dict[str, list[Any]],
    config: Optional[BacktestConfig] = None,
    metric: str = "sharpe",
    n_splits: int = 4,
    train_frac: float = 0.7,
    fixed: Optional[dict[str, Any]] = None,
) -> pd.DataFrame:
    """Rolling windows: optimise on the train part of each window, evaluate the best params on the next test part."""
    if not 0 < train_frac < 1:
        raise ValueError("train_frac debe estar en (0, 1)")
    window = len(df) // n_splits
    rows = []
    for k in range(n_splits):
        chunk = df.iloc[k * window : (k + 1) * window if k < n_splits - 1 else len(df)]
        cut = int(len(chunk) * train_frac)
        train, test = chunk.iloc[:cut], chunk.iloc[cut:]
        ranked = grid_search(strategy_cls, train, grid, config, metric, fixed)
        if ranked.empty or len(test) < 2:
            continue
        best = {p: ranked.iloc[0][p] for p in grid}
        best = {p: (type(grid[p][0])(v) if not isinstance(grid[p][0], bool) else bool(v)) for p, v in best.items()}
        # Warm the indicators up with train data but only score the test period.
        warm = strategy_cls(**{**(fixed or {}), **best})
        res = run_backtest(warm, pd.concat([train.iloc[-warm.warmup :], test]), config)
        eq = res.equity.loc[test.index[0] :]
        oos_ret = float(eq.iloc[-1] / eq.iloc[0] - 1)
        rows.append({
            "split": k,
            "train_start": train.index[0], "test_start": test.index[0], "test_end": test.index[-1],
            **best,
            f"is_{metric}": float(ranked.iloc[0][metric]),
            "oos_return": oos_ret,
            "oos_buy_hold": float(test["close"].iloc[-1] / test["close"].iloc[0] - 1),
        })
    return pd.DataFrame(rows)
