"""Pre-trade risk checks and kill switch for live/paper execution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# Hyperliquid rejects orders below $10 notional.
MIN_ORDER_NOTIONAL = 10.0


@dataclass
class RiskLimits:
    max_leverage: float = 3.0  # hard cap on |position notional| / equity
    max_position_usd: float = 50_000.0
    max_daily_loss: float = 0.05  # fraction of the day's starting equity; breach => flatten and halt
    min_order_usd: float = MIN_ORDER_NOTIONAL
    rebalance_tolerance: float = 0.05  # ignore size changes smaller than 5% of the target


class RiskManager:
    def __init__(self, limits: RiskLimits):
        self.limits = limits
        self.halted = False
        self.halt_reason: Optional[str] = None
        self._day: Optional[str] = None
        self._day_start_equity: Optional[float] = None

    def update_equity(self, equity: float, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        if day != self._day:
            self._day, self._day_start_equity = day, equity
        if self._day_start_equity and equity < self._day_start_equity * (1 - self.limits.max_daily_loss):
            self.halt(f"pérdida diaria > {self.limits.max_daily_loss:.1%} (equity {equity:.2f} vs {self._day_start_equity:.2f})")

    def halt(self, reason: str) -> None:
        self.halted, self.halt_reason = True, reason

    def clamp_target(self, target_qty: float, price: float, equity: float) -> float:
        """Apply leverage and notional caps to a desired position size (in coin units)."""
        if self.halted or price <= 0 or equity <= 0:
            return 0.0
        max_notional = min(self.limits.max_position_usd, self.limits.max_leverage * equity)
        notional = abs(target_qty) * price
        if notional > max_notional:
            target_qty = max_notional / price * (1 if target_qty > 0 else -1)
        return target_qty

    def order_delta(self, current_qty: float, target_qty: float, price: float) -> float:
        """Size to trade, or 0 if the change is too small to bother (or below exchange minimum)."""
        delta = target_qty - current_qty
        if target_qty == 0 and current_qty != 0:
            return delta  # always allow closing
        if abs(delta) * price < self.limits.min_order_usd:
            return 0.0
        if target_qty != 0 and abs(delta) < abs(target_qty) * self.limits.rebalance_tolerance:
            return 0.0
        return delta
