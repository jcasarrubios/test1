"""Execution backends: a paper broker (simulated fills) and the real Hyperliquid exchange."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from .data import MAINNET_API_URL, TESTNET_API_URL

log = logging.getLogger(__name__)


@dataclass
class Fill:
    coin: str
    size: float  # signed
    price: float
    fee: float
    reason: str = ""


class Broker(Protocol):
    def equity(self) -> float: ...

    def position(self, coin: str) -> float: ...

    def entry_price(self, coin: str) -> float: ...

    def market_order(self, coin: str, size: float, ref_price: float, reason: str = "") -> Optional[Fill]: ...


class PaperBroker:
    """Simulated account. Fills market orders at ref_price +/- slippage and charges taker fees.

    State is optionally persisted to a JSON file so a paper session survives restarts.
    """

    def __init__(self, initial_equity: float = 10_000.0, taker_fee: float = 0.00045, slippage_bps: float = 2.0,
                 state_file: Optional[str | Path] = None):
        self.taker_fee = taker_fee
        self.slippage = slippage_bps / 10_000
        self.state_file = Path(state_file) if state_file else None
        self.cash = initial_equity  # realized equity (initial + realized pnl - fees)
        self.positions: dict[str, dict] = {}  # coin -> {qty, avg}
        self.marks: dict[str, float] = {}
        self.fills: list[Fill] = []
        if self.state_file and self.state_file.exists():
            st = json.loads(self.state_file.read_text())
            self.cash, self.positions, self.marks = st["cash"], st["positions"], st.get("marks", {})

    def _save(self) -> None:
        if self.state_file:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"cash": self.cash, "positions": self.positions, "marks": self.marks}, indent=2))

    def mark(self, coin: str, price: float) -> None:
        self.marks[coin] = price

    def equity(self) -> float:
        upnl = sum(p["qty"] * (self.marks.get(c, p["avg"]) - p["avg"]) for c, p in self.positions.items())
        return self.cash + upnl

    def position(self, coin: str) -> float:
        return self.positions.get(coin, {}).get("qty", 0.0)

    def entry_price(self, coin: str) -> float:
        return self.positions.get(coin, {}).get("avg", 0.0)

    def market_order(self, coin: str, size: float, ref_price: float, reason: str = "") -> Optional[Fill]:
        if size == 0:
            return None
        px = ref_price * (1 + self.slippage if size > 0 else 1 - self.slippage)
        fee = abs(size) * px * self.taker_fee
        self.cash -= fee
        p = self.positions.get(coin, {"qty": 0.0, "avg": 0.0})
        q = p["qty"]
        if q == 0 or (q > 0) == (size > 0):
            p["avg"] = (q * p["avg"] + size * px) / (q + size)
            p["qty"] = q + size
        else:
            closed = min(abs(size), abs(q))
            self.cash += closed * (px - p["avg"]) * (1 if q > 0 else -1)
            new_q = q + size
            if abs(new_q) < 1e-12:
                p = {"qty": 0.0, "avg": 0.0}
            elif (new_q > 0) != (q > 0):
                p = {"qty": new_q, "avg": px}
            else:
                p["qty"] = new_q
        if p["qty"] == 0:
            self.positions.pop(coin, None)
        else:
            self.positions[coin] = p
        self.marks[coin] = ref_price
        fill = Fill(coin, size, px, fee, reason)
        self.fills.append(fill)
        self._save()
        return fill


class HyperliquidBroker:
    """Real execution through the official `hyperliquid-python-sdk`.

    Credentials: an API/agent wallet private key (recommended: create one at app.hyperliquid.xyz/API so
    the key cannot withdraw) and the main account address that holds the funds.
    """

    def __init__(self, private_key: str, account_address: Optional[str] = None, testnet: bool = True,
                 slippage: float = 0.01, vault_address: Optional[str] = None):
        try:
            import eth_account
            from hyperliquid.exchange import Exchange
            from hyperliquid.info import Info
        except ImportError as e:  # pragma: no cover
            raise ImportError("Instala las dependencias live: pip install -e '.[live]'") from e
        self.base_url = TESTNET_API_URL if testnet else MAINNET_API_URL
        wallet = eth_account.Account.from_key(private_key)
        self.address = account_address or wallet.address
        self.slippage = slippage
        self.info = Info(self.base_url, skip_ws=True)
        self.exchange = Exchange(wallet, self.base_url, account_address=account_address, vault_address=vault_address)
        self._sz_decimals = {a["name"]: a["szDecimals"] for a in self.info.meta()["universe"]}
        log.info("Conectado a %s como %s (firmante %s)", self.base_url, self.address, wallet.address)

    def _state(self) -> dict:
        return self.info.user_state(self.address)

    def equity(self) -> float:
        return float(self._state()["marginSummary"]["accountValue"])

    def _pos(self, coin: str) -> dict:
        for ap in self._state()["assetPositions"]:
            if ap["position"]["coin"] == coin:
                return ap["position"]
        return {}

    def position(self, coin: str) -> float:
        return float(self._pos(coin).get("szi", 0.0))

    def entry_price(self, coin: str) -> float:
        return float(self._pos(coin).get("entryPx") or 0.0)

    def round_size(self, coin: str, size: float) -> float:
        return round(size, self._sz_decimals[coin])

    def set_leverage(self, coin: str, leverage: int, cross: bool = True):
        return self.exchange.update_leverage(leverage, coin, cross)

    def market_order(self, coin: str, size: float, ref_price: float, reason: str = "") -> Optional[Fill]:
        size = self.round_size(coin, size)
        if size == 0:
            return None
        current = self.position(coin)
        reduce_only = current != 0 and (current > 0) != (size > 0) and abs(size) <= abs(current) + 1e-12
        if reduce_only:
            resp = self.exchange.market_close(coin, sz=abs(size), slippage=self.slippage)
        else:
            resp = self.exchange.market_open(coin, size > 0, abs(size), None, self.slippage)
        log.info("Orden %s %s %s -> %s", coin, size, reason, resp)
        if not resp or resp.get("status") != "ok":
            raise RuntimeError(f"Orden rechazada: {resp}")
        filled_sz, avg_px = 0.0, ref_price
        for st in resp["response"]["data"]["statuses"]:
            if "error" in st:
                raise RuntimeError(f"Orden rechazada: {st['error']}")
            if "filled" in st:
                filled_sz = float(st["filled"]["totalSz"])
                avg_px = float(st["filled"]["avgPx"])
        if filled_sz == 0:
            return None
        signed = filled_sz if size > 0 else -filled_sz
        return Fill(coin, signed, avg_px, 0.0, reason)
