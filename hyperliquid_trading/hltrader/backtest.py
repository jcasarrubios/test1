"""Event-driven bar backtester for perpetual futures.

Model:
- The signal computed at the close of bar t is executed at the open of bar t+1 (no look-ahead).
- Target notional = signal * leverage * equity. The position is only re-sized when the signal changes.
- Market orders pay taker fee + slippage. Stop-loss / take-profit are evaluated intrabar with the high/low
  (if both are hit in the same bar the stop is assumed first, which is the conservative choice).
- Funding: longs pay and shorts receive `rate * notional` every hour (positive rate), prorated per bar.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .data import bars_per_year, interval_to_ms
from .metrics import compute_metrics
from .strategy import Strategy

# Hyperliquid base tier (perps): taker 0.045%, maker 0.015%.
DEFAULT_TAKER_FEE = 0.00045


@dataclass
class BacktestConfig:
    initial_equity: float = 10_000.0
    leverage: float = 1.0
    taker_fee: float = DEFAULT_TAKER_FEE
    slippage_bps: float = 1.0
    stop_loss: Optional[float] = None  # e.g. 0.03 = 3% adverse move from entry
    take_profit: Optional[float] = None  # e.g. 0.06 = 6% favourable move
    funding_rate_hourly: float = 0.0  # constant rate used when no funding series is supplied
    interval: str = "1h"


@dataclass
class BacktestResult:
    strategy: str
    config: BacktestConfig
    equity: pd.Series
    position: pd.Series
    signals: pd.Series
    trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)

    def summary(self) -> str:
        m = self.metrics
        lines = [
            f"Estrategia: {self.strategy}",
            f"Periodo: {self.equity.index[0]} -> {self.equity.index[-1]} ({len(self.equity)} barras)",
            f"Equity final: {m['final_equity']:,.2f}  (inicial {self.config.initial_equity:,.2f})",
            f"Retorno total: {m['total_return']:.2%}   Buy&Hold: {m['buy_hold_return']:.2%}",
            f"CAGR: {m['cagr']:.2%}   Sharpe: {m['sharpe']:.2f}   Sortino: {m['sortino']:.2f}   Calmar: {m['calmar']:.2f}",
            f"Max drawdown: {m['max_drawdown']:.2%}   Exposición: {m['exposure']:.1%}",
            f"Trades: {m['n_trades']}   Win rate: {m['win_rate']:.1%}   Profit factor: {m['profit_factor']:.2f}",
            f"Comisiones: {m['fees_paid']:,.2f}   Funding: {m['funding_paid']:,.2f}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"strategy": self.strategy, "config": asdict(self.config), "metrics": self.metrics}


class _Book:
    """Position accounting with average entry price and round-trip trade tracking."""

    def __init__(self, fee: float):
        self.fee = fee
        self.qty = 0.0
        self.avg = 0.0
        self.realized = 0.0
        self.fees = 0.0
        self.funding = 0.0
        self.trades: list[dict] = []
        self._open: Optional[dict] = None

    def fill(self, delta: float, price: float, ts, reason: str) -> None:
        if delta == 0:
            return
        fee = abs(delta) * price * self.fee
        self.fees += fee
        q = self.qty
        if q == 0 or np.sign(q) == np.sign(delta):
            if q == 0:
                self._open = {"entry_time": ts, "side": "long" if delta > 0 else "short", "entry_price": price,
                              "size": 0.0, "pnl": 0.0, "fees": 0.0}
            self.avg = (q * self.avg + delta * price) / (q + delta)
            self.qty = q + delta
            self._open["size"] = max(self._open["size"], abs(self.qty))
            self._open["fees"] += fee
            return
        # Reducing / closing / flipping.
        closed = min(abs(delta), abs(q))
        pnl = closed * (price - self.avg) * np.sign(q)
        self.realized += pnl
        self._open["pnl"] += pnl
        self._open["fees"] += fee * closed / abs(delta)
        remaining = q + delta
        if abs(remaining) < 1e-12 or np.sign(remaining) != np.sign(q):
            self._close_trade(price, ts, reason)
            self.qty, self.avg = 0.0, 0.0
            if abs(remaining) > 1e-12:  # flip: open the rest on the other side
                self.fees -= fee * (abs(remaining) / abs(delta))
                self.fill(remaining, price, ts, reason)
        else:
            self.qty = remaining

    def _close_trade(self, price, ts, reason):
        t = self._open
        t.update(exit_time=ts, exit_price=price, exit_reason=reason)
        t["net_pnl"] = t["pnl"] - t["fees"]
        self.trades.append(t)
        self._open = None

    def equity(self, initial: float, price: float) -> float:
        return initial + self.realized - self.fees - self.funding + self.qty * (price - self.avg)


def run_backtest(
    strategy: Strategy,
    df: pd.DataFrame,
    config: Optional[BacktestConfig] = None,
    funding: Optional[pd.Series] = None,
) -> BacktestResult:
    cfg = config or BacktestConfig()
    if len(df) < 2:
        raise ValueError("Se necesitan al menos 2 barras para el backtest")
    sig = strategy.signals(df).to_numpy()
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    idx = df.index
    bar_hours = interval_to_ms(cfg.interval) / 3_600_000
    if funding is not None and len(funding):
        fund = funding.sort_index().reindex(idx, method="ffill").fillna(0.0).to_numpy()
    else:
        fund = np.full(len(df), cfg.funding_rate_hourly)
    slip = cfg.slippage_bps / 10_000

    book = _Book(cfg.taker_fee)
    equity = np.empty(len(df))
    pos = np.zeros(len(df))
    equity[0] = cfg.initial_equity
    current_sig = 0.0
    stopped_sig: Optional[float] = None  # after a SL/TP, stay flat until the signal changes
    busted = False

    for i in range(1, len(df)):
        ts = idx[i]
        desired = sig[i - 1]
        if stopped_sig is not None:
            if desired == stopped_sig:
                desired = 0.0
            else:
                stopped_sig = None
        if not busted and desired != current_sig:
            eq = book.equity(cfg.initial_equity, o[i])
            target_qty = desired * cfg.leverage * max(eq, 0.0) / o[i]
            delta = target_qty - book.qty
            if abs(delta) > 1e-12:
                px = o[i] * (1 + slip * np.sign(delta))
                book.fill(delta, px, ts, "signal")
            current_sig = desired

        # Intrabar stop-loss / take-profit.
        if book.qty != 0 and (cfg.stop_loss or cfg.take_profit):
            long = book.qty > 0
            exit_px, reason = None, None
            if cfg.stop_loss:
                stop = book.avg * (1 - cfg.stop_loss) if long else book.avg * (1 + cfg.stop_loss)
                if (long and l[i] <= stop) or (not long and h[i] >= stop):
                    exit_px, reason = (min(o[i], stop) if long else max(o[i], stop)), "stop_loss"
            if exit_px is None and cfg.take_profit:
                tp = book.avg * (1 + cfg.take_profit) if long else book.avg * (1 - cfg.take_profit)
                if (long and h[i] >= tp) or (not long and l[i] <= tp):
                    exit_px, reason = (max(o[i], tp) if long else min(o[i], tp)), "take_profit"
            if exit_px is not None:
                delta = -book.qty
                book.fill(delta, exit_px * (1 + slip * np.sign(delta)), ts, reason)
                stopped_sig, current_sig = current_sig, 0.0

        if book.qty != 0:
            book.funding += book.qty * c[i] * fund[i] * bar_hours

        equity[i] = book.equity(cfg.initial_equity, c[i])
        if equity[i] <= 0 and not busted:
            if book.qty != 0:
                book.fill(-book.qty, c[i], ts, "liquidation")
            busted = True
            equity[i] = book.equity(cfg.initial_equity, c[i])
        pos[i] = book.qty

    if book.qty != 0 and book._open is not None:  # mark open trade at the last close
        open_t = dict(book._open)
        open_t.update(exit_time=idx[-1], exit_price=c[-1], exit_reason="open",
                      pnl=open_t["pnl"] + book.qty * (c[-1] - book.avg))
        open_t["net_pnl"] = open_t["pnl"] - open_t["fees"]
        book.trades.append(open_t)

    equity_s = pd.Series(equity, index=idx, name="equity")
    position_s = pd.Series(pos, index=idx, name="position")
    trades = pd.DataFrame(book.trades)
    metrics = compute_metrics(equity_s, trades, df["close"], position_s, bars_per_year(cfg.interval))
    metrics["fees_paid"] = book.fees
    metrics["funding_paid"] = book.funding
    return BacktestResult(repr(strategy), cfg, equity_s, position_s, pd.Series(sig, index=idx, name="signal"), trades, metrics)
