"""Live / paper execution loop.

Every poll:
1. Download the latest closed candles and the mid price.
2. Update equity and the risk manager (daily-loss kill switch).
3. Check stop-loss / take-profit against the mid price.
4. When a new candle has closed, recompute the strategy signal and, if it changed, trade towards the
   target position (same sizing rule as the backtester: signal * leverage * equity).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from .brokers import Broker, PaperBroker
from .data import CandleSource, drop_unclosed
from .risk import RiskManager
from .strategy import Strategy

log = logging.getLogger(__name__)


@dataclass
class RunnerConfig:
    coin: str = "BTC"
    interval: str = "1h"
    leverage: float = 1.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    poll_seconds: float = 30.0
    extra_bars: int = 50
    drop_forming_candle: bool = True
    journal: Optional[str] = "runs/journal.jsonl"


class Runner:
    def __init__(self, strategy: Strategy, source: CandleSource, broker: Broker, risk: RiskManager, config: RunnerConfig):
        self.strategy = strategy
        self.source = source
        self.broker = broker
        self.risk = risk
        self.cfg = config
        self.last_bar: Optional[pd.Timestamp] = None
        self.last_signal: Optional[float] = None
        self.stopped_signal: Optional[float] = None
        self._journal = Path(config.journal) if config.journal else None

    def _record(self, event: str, **data) -> dict:
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, "coin": self.cfg.coin, **data}
        log.info("%s %s", event, {k: v for k, v in data.items()})
        if self._journal:
            self._journal.parent.mkdir(parents=True, exist_ok=True)
            with self._journal.open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        return rec

    def _trade(self, delta: float, price: float, reason: str) -> None:
        fill = self.broker.market_order(self.cfg.coin, delta, price, reason)
        if fill:
            self._record("fill", size=fill.size, price=fill.price, fee=fill.fee, reason=reason)

    def _check_exits(self, price: float) -> bool:
        qty = self.broker.position(self.cfg.coin)
        if qty == 0 or not (self.cfg.stop_loss or self.cfg.take_profit):
            return False
        entry = self.broker.entry_price(self.cfg.coin)
        move = (price / entry - 1) * (1 if qty > 0 else -1)
        reason = None
        if self.cfg.stop_loss and move <= -self.cfg.stop_loss:
            reason = "stop_loss"
        elif self.cfg.take_profit and move >= self.cfg.take_profit:
            reason = "take_profit"
        if reason:
            self._trade(-qty, price, reason)
            self.stopped_signal = self.last_signal
            return True
        return False

    def step(self, now_ms: Optional[int] = None) -> None:
        coin = self.cfg.coin
        price = self.source.mid_price(coin)
        if isinstance(self.broker, PaperBroker):
            self.broker.mark(coin, price)
        equity = self.broker.equity()
        self.risk.update_equity(equity)
        if self.risk.halted:
            qty = self.broker.position(coin)
            if qty != 0:
                self._record("halt", reason=self.risk.halt_reason)
                self._trade(-qty, price, "kill_switch")
            return

        self._check_exits(price)

        n = self.strategy.warmup + self.cfg.extra_bars
        df = self.source.fetch_recent(coin, self.cfg.interval, n)
        if self.cfg.drop_forming_candle:
            df = drop_unclosed(df, self.cfg.interval, now_ms)
        if df.empty or (self.last_bar is not None and df.index[-1] <= self.last_bar):
            return
        self.last_bar = df.index[-1]
        if len(df) < self.strategy.warmup:
            log.warning("Solo %d barras, la estrategia pide %d de warmup", len(df), self.strategy.warmup)

        signal = float(self.strategy.signals(df).iloc[-1])
        if self.stopped_signal is not None:
            if signal == self.stopped_signal:
                signal = 0.0
            else:
                self.stopped_signal = None
        self._record("bar", time=self.last_bar, close=float(df["close"].iloc[-1]), signal=signal, equity=equity,
                     position=self.broker.position(coin))

        current = self.broker.position(coin)
        in_line = (signal > 0 and current > 0) or (signal < 0 and current < 0) or (signal == 0 and current == 0)
        if signal == self.last_signal or (self.last_signal is None and in_line):
            self.last_signal = signal
            return  # unchanged signal (or position already matches after a restart): don't churn
        target = signal * self.cfg.leverage * equity / price
        target = self.risk.clamp_target(target, price, equity)
        delta = self.risk.order_delta(current, target, price)
        if delta:
            self._trade(delta, price, "signal")
        self.last_signal = signal

    def run(self, max_steps: Optional[int] = None) -> None:
        self._record("start", strategy=repr(self.strategy), config=self.cfg.__dict__)
        steps = 0
        try:
            while max_steps is None or steps < max_steps:
                try:
                    self.step()
                except Exception as e:  # keep the loop alive on transient API errors
                    log.exception("Error en step")
                    self._record("error", error=str(e))
                steps += 1
                time.sleep(self.cfg.poll_seconds)
        except KeyboardInterrupt:
            self._record("stop", reason="keyboard")

