from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

from hltrader import get_strategy
from hltrader.backtest import BacktestConfig, run_backtest
from hltrader.brokers import HyperliquidBroker, PaperBroker
from hltrader.cli import main, parse_grid, parse_params
from hltrader.data import ReplaySource, save_csv
from hltrader.risk import RiskLimits, RiskManager
from hltrader.runner import Runner, RunnerConfig
from hltrader.strategy import Strategy


def test_paper_broker_pnl_and_persistence(tmp_path):
    state = tmp_path / "s.json"
    b = PaperBroker(1000, taker_fee=0.0, slippage_bps=0, state_file=state)
    b.market_order("BTC", 2, 100)
    b.mark("BTC", 110)
    assert b.equity() == pytest.approx(1020)
    b.market_order("BTC", -3, 110)  # close +20, open 1 short @110
    assert b.position("BTC") == -1 and b.entry_price("BTC") == 110
    b2 = PaperBroker(state_file=state)
    assert b2.position("BTC") == -1 and b2.cash == pytest.approx(1020)


def test_paper_broker_fees():
    b = PaperBroker(1000, taker_fee=0.001, slippage_bps=10)
    f = b.market_order("ETH", 1, 100)
    assert f.price == pytest.approx(100.1) and f.fee == pytest.approx(0.1001)


def test_risk_clamp_and_min_order():
    r = RiskManager(RiskLimits(max_leverage=2, max_position_usd=1500, min_order_usd=10))
    assert r.clamp_target(100, 100, 1000) == pytest.approx(15)  # capped by max_position_usd
    assert r.clamp_target(-100, 100, 500) == pytest.approx(-10)  # capped by 2x leverage
    assert r.order_delta(0, 0.05, 100) == 0  # $5 < exchange minimum
    assert r.order_delta(1, 0, 100) == -1  # closing is always allowed
    assert r.order_delta(10, 10.2, 100) == 0  # within rebalance tolerance


def test_kill_switch():
    r = RiskManager(RiskLimits(max_daily_loss=0.05))
    d = datetime(2024, 1, 1, tzinfo=timezone.utc)
    r.update_equity(1000, d)
    r.update_equity(960, d)
    assert not r.halted
    r.update_equity(940, d)
    assert r.halted and r.clamp_target(1, 100, 940) == 0


def _replay(candles, strategy, stop_loss=None, limits=None):
    src = ReplaySource(candles, start=strategy.warmup)
    broker = PaperBroker(10_000, taker_fee=0.00045, slippage_bps=1)
    cfg = RunnerConfig(drop_forming_candle=False, journal=None, stop_loss=stop_loss)
    runner = Runner(strategy, src, broker, RiskManager(limits or RiskLimits(max_leverage=5, max_position_usd=1e9, max_daily_loss=0.5)), cfg)
    while src.advance():
        runner.step()
    return broker


def test_replay_matches_backtest_trade_count(candles):
    strat = get_strategy("donchian_breakout")(entry=30, exit=10)
    broker = _replay(candles, strat)
    bt = run_backtest(strat, candles.iloc[strat.warmup - 1 :], BacktestConfig())
    n_bt_fills = sum(2 if r.exit_reason != "open" else 1 for r in bt.trades.itertuples())
    assert len(broker.fills) > 5
    assert abs(len(broker.fills) - n_bt_fills) <= 2
    assert broker.equity() == pytest.approx(bt.equity.iloc[-1], rel=0.05)


def test_runner_stop_loss_and_no_churn(candles):
    class AlwaysLong(Strategy):
        name = ""
        defaults = {}
        warmup = 5

        def generate(self, df):
            return pd.Series(1.0, index=df.index)

    closes = [100] * 10 + [99, 98, 94, 93, 95, 100]
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1.0},
                      index=pd.date_range("2024", periods=len(closes), freq="1h", tz="UTC"))
    broker = _replay(df, AlwaysLong(), stop_loss=0.05)
    reasons = [f.reason for f in broker.fills]
    assert reasons == ["signal", "stop_loss"]  # entered once, stopped, did not re-enter on the same signal


def test_hyperliquid_broker_order_parsing():
    b = HyperliquidBroker.__new__(HyperliquidBroker)
    b.address, b.slippage, b._sz_decimals = "0xabc", 0.01, {"BTC": 3}
    calls = []
    ok = {"status": "ok", "response": {"data": {"statuses": [{"filled": {"totalSz": "0.012", "avgPx": "65000", "oid": 1}}]}}}
    b.exchange = SimpleNamespace(market_open=lambda *a: calls.append(("open", a)) or ok,
                                 market_close=lambda coin, sz, slippage: calls.append(("close", coin, sz)) or ok)
    b.info = SimpleNamespace(user_state=lambda addr: {"assetPositions": []})
    fill = b.market_order("BTC", 0.01234, 65000)
    assert calls[0] == ("open", ("BTC", True, 0.012, None, 0.01))
    assert fill.size == pytest.approx(0.012) and fill.price == 65000
    b.info = SimpleNamespace(user_state=lambda addr: {"assetPositions": [{"position": {"coin": "BTC", "szi": "0.012"}}]})
    b.market_order("BTC", -0.012, 65000)
    assert calls[1] == ("close", "BTC", 0.012)
    b.exchange = SimpleNamespace(market_open=lambda *a: {"status": "ok", "response": {"data": {"statuses": [{"error": "no margin"}]}}})
    b.info = SimpleNamespace(user_state=lambda addr: {"assetPositions": []})
    with pytest.raises(RuntimeError):
        b.market_order("BTC", 1, 65000)


def test_cli_parsers():
    assert parse_params(["fast=5", "k=2.5", "allow_short=false", "x=abc"]) == {"fast": 5, "k": 2.5, "allow_short": False, "x": "abc"}
    assert parse_grid(["fast=5,10", "slow=50"]) == {"fast": [5, 10], "slow": [50]}


def test_cli_end_to_end(tmp_path, candles, capsys):
    main(["backtest", "-s", "ema_cross", "--synthetic", "800", "-p", "fast=8", "--out", str(tmp_path / "bt")])
    assert (tmp_path / "bt" / "trades.csv").exists()
    main(["optimize", "-s", "rsi_reversion", "--synthetic", "800", "-g", "period=7,14", "--min-trades", "1"])
    save_csv(candles, tmp_path / "c.csv")
    conf = yaml.safe_load((Path(__file__).parents[1] / "configs" / "example.yaml").read_text())
    conf["runner"]["journal"] = str(tmp_path / "journal.jsonl")
    (tmp_path / "conf.yaml").write_text(yaml.safe_dump(conf))
    main(["run", "--replay", str(tmp_path / "c.csv"), "-c", str(tmp_path / "conf.yaml")])
    assert (tmp_path / "journal.jsonl").read_text().count('"event": "fill"') > 0
    out = capsys.readouterr().out
    assert "Sharpe" in out and "Replay terminado" in out
