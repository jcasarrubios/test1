"""Performance metrics."""
from __future__ import annotations

import numpy as np
import pandas as pd


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float((equity / peak - 1).min()) if len(equity) else 0.0


def compute_metrics(
    equity: pd.Series,
    trades: pd.DataFrame,
    close: pd.Series,
    position: pd.Series,
    periods_per_year: float,
) -> dict:
    rets = equity.pct_change().fillna(0.0)
    initial, final = float(equity.iloc[0]), float(equity.iloc[-1])
    total_return = final / initial - 1
    years = len(equity) / periods_per_year
    with np.errstate(over="ignore"):
        cagr = float(np.power(final / initial, 1 / years)) - 1 if years > 0 and final > 0 else -1.0
    std = rets.std()
    sharpe = float(rets.mean() / std * np.sqrt(periods_per_year)) if std > 0 else 0.0
    downside = rets[rets < 0].std()
    sortino = float(rets.mean() / downside * np.sqrt(periods_per_year)) if downside and downside > 0 else 0.0
    mdd = max_drawdown(equity)
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0

    closed = trades[trades["exit_reason"] != "open"] if len(trades) else trades
    n = len(closed)
    if n:
        wins = closed["net_pnl"] > 0
        gross_win = closed.loc[wins, "net_pnl"].sum()
        gross_loss = -closed.loc[~wins, "net_pnl"].sum()
        win_rate = float(wins.mean())
        profit_factor = float(gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
        avg_trade = float(closed["net_pnl"].mean())
    else:
        win_rate, profit_factor, avg_trade = 0.0, 0.0, 0.0

    return {
        "initial_equity": initial,
        "final_equity": final,
        "total_return": total_return,
        "buy_hold_return": float(close.iloc[-1] / close.iloc[0] - 1),
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "calmar": calmar,
        "volatility": float(std * np.sqrt(periods_per_year)),
        "n_trades": n,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_trade_pnl": avg_trade,
        "exposure": float((position != 0).mean()),
    }
