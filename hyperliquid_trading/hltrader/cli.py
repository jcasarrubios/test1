"""Command line interface: hltrader {strategies,fetch,backtest,optimize,run}."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .backtest import BacktestConfig, run_backtest
from .data import (
    MAINNET_API_URL,
    TESTNET_API_URL,
    HyperliquidData,
    ReplaySource,
    load_csv,
    load_or_fetch,
    synthetic_candles,
)
from .optimize import grid_search, walk_forward
from .strategy import get_strategy, list_strategies


def parse_value(v: str) -> Any:
    low = v.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return v


def parse_params(items: list[str] | None) -> dict[str, Any]:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"Parámetro inválido '{item}', usa clave=valor")
        k, v = item.split("=", 1)
        out[k.strip()] = parse_value(v)
    return out


def parse_grid(items: list[str] | None) -> dict[str, list[Any]]:
    out = {}
    for item in items or []:
        k, v = item.split("=", 1)
        out[k.strip()] = [parse_value(x) for x in v.split(",") if x.strip()]
    return out


def add_data_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--coin", default="BTC")
    p.add_argument("--interval", default="1h")
    p.add_argument("--days", type=float, default=180)
    p.add_argument("--data-dir", default="data")
    p.add_argument("--csv", help="Usar un CSV OHLCV propio en vez de descargar")
    p.add_argument("--synthetic", type=int, metavar="N", help="Usar N velas sintéticas (sin red)")
    p.add_argument("--testnet", action="store_true", help="Datos de testnet")
    p.add_argument("--refresh", action="store_true", help="Ignorar la caché y descargar de nuevo")


def add_bt_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--equity", type=float, default=10_000)
    p.add_argument("--leverage", type=float, default=1.0)
    p.add_argument("--fee", type=float, default=0.00045, help="Comisión taker (0.00045 = 0.045%%)")
    p.add_argument("--slippage-bps", type=float, default=1.0)
    p.add_argument("--stop-loss", type=float)
    p.add_argument("--take-profit", type=float)
    p.add_argument("--funding", type=float, default=0.0, help="Funding horario constante (ej. 0.0000125)")


def load_data(args) -> pd.DataFrame:
    if args.synthetic:
        return synthetic_candles(args.synthetic, args.interval)
    if args.csv:
        return load_csv(args.csv)
    client = HyperliquidData(TESTNET_API_URL if args.testnet else MAINNET_API_URL)
    return load_or_fetch(args.coin, args.interval, args.days, args.data_dir, client, args.refresh)


def bt_config(args) -> BacktestConfig:
    return BacktestConfig(
        initial_equity=args.equity, leverage=args.leverage, taker_fee=args.fee, slippage_bps=args.slippage_bps,
        stop_loss=args.stop_loss, take_profit=args.take_profit, funding_rate_hourly=args.funding, interval=args.interval,
    )


def cmd_strategies(args) -> None:
    for name, cls in list_strategies().items():
        print(f"{name}\n  {cls.description}\n  parámetros: {cls.defaults}\n")


def cmd_fetch(args) -> None:
    df = load_data(args)
    print(f"{len(df)} velas {args.coin} {args.interval}: {df.index[0]} -> {df.index[-1]}")


def cmd_backtest(args) -> None:
    df = load_data(args)
    strat = get_strategy(args.strategy)(**parse_params(args.param))
    res = run_backtest(strat, df, bt_config(args))
    print(res.summary())
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"close": df["close"], "signal": res.signals, "position": res.position, "equity": res.equity}).to_csv(out / "equity.csv")
        res.trades.to_csv(out / "trades.csv", index=False)
        (out / "result.json").write_text(json.dumps(res.to_dict(), indent=2, default=str))
        print(f"\nResultados guardados en {out}/")


def cmd_optimize(args) -> None:
    df = load_data(args)
    cls = get_strategy(args.strategy)
    grid = parse_grid(args.grid)
    if not grid:
        raise SystemExit("Indica al menos un --grid clave=v1,v2,...")
    fixed = parse_params(args.param)
    pd.set_option("display.width", 200)
    if args.walk_forward:
        wf = walk_forward(cls, df, grid, bt_config(args), args.metric, args.walk_forward, args.train_frac, fixed)
        print(wf.to_string(index=False))
        if not wf.empty:
            compounded = (1 + wf["oos_return"]).prod() - 1
            bh = (1 + wf["oos_buy_hold"]).prod() - 1
            print(f"\nRetorno out-of-sample compuesto: {compounded:.2%}  (buy&hold en los mismos tramos: {bh:.2%})")
    else:
        ranked = grid_search(cls, df, grid, bt_config(args), args.metric, fixed, args.min_trades)
        print(ranked.head(args.top).to_string(index=False))


def load_run_config(path: str | None) -> dict:
    if not path:
        return {}
    return yaml.safe_load(Path(path).read_text()) or {}


def cmd_run(args) -> None:
    from .brokers import HyperliquidBroker, PaperBroker
    from .risk import RiskLimits, RiskManager
    from .runner import Runner, RunnerConfig

    conf = load_run_config(args.config)
    strat_conf = conf.get("strategy", {})
    name = args.strategy or strat_conf.get("name")
    if not name:
        raise SystemExit("Indica --strategy o strategy.name en el config")
    params = {**strat_conf.get("params", {}), **parse_params(args.param)}
    strategy = get_strategy(name)(**params)

    rc = {**conf.get("runner", {})}
    for key in ("coin", "interval", "leverage", "stop_loss", "take_profit", "poll_seconds"):
        v = getattr(args, key)
        if v is not None:
            rc[key] = v
    runner_cfg = RunnerConfig(**rc)
    limits = RiskLimits(**conf.get("risk", {}))
    mode = args.mode or conf.get("mode", "paper")
    testnet = not args.mainnet and conf.get("network", "testnet") != "mainnet"

    if args.replay:
        source = ReplaySource(load_csv(args.replay), start=strategy.warmup + runner_cfg.extra_bars)
        runner_cfg.drop_forming_candle = False
        runner_cfg.poll_seconds = 0
    else:
        source = HyperliquidData(TESTNET_API_URL if testnet else MAINNET_API_URL)

    if mode == "paper":
        broker = PaperBroker(initial_equity=args.equity, state_file=conf.get("paper_state_file", "runs/paper_state.json")
                             if not args.replay else None)
    elif mode == "live":
        if args.replay:
            raise SystemExit("--replay solo es compatible con --mode paper")
        key = os.environ.get("HL_PRIVATE_KEY")
        if not key:
            raise SystemExit("Define HL_PRIVATE_KEY (clave de una API wallet) y opcionalmente HL_ACCOUNT_ADDRESS")
        if not testnet and not args.i_understand_the_risks:
            raise SystemExit("Mainnet requiere --i-understand-the-risks")
        broker = HyperliquidBroker(key, os.environ.get("HL_ACCOUNT_ADDRESS"), testnet=testnet)
        broker.set_leverage(runner_cfg.coin, max(1, int(round(min(limits.max_leverage, max(runner_cfg.leverage, 1))))))
    else:
        raise SystemExit(f"Modo desconocido: {mode}")

    runner = Runner(strategy, source, broker, RiskManager(limits), runner_cfg)
    print(f"Ejecutando {strategy} en {runner_cfg.coin} {runner_cfg.interval} | modo={mode} | "
          f"{'replay' if args.replay else ('testnet' if testnet else 'MAINNET')}")
    if args.replay:
        runner._record("start", strategy=repr(strategy), config=runner_cfg.__dict__)
        while source.advance():
            runner.step()
        print(f"Replay terminado. Equity final: {broker.equity():,.2f}  fills: {len(broker.fills)}")
    else:
        runner.run(args.max_steps)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hltrader", description="Trading de estrategias en Hyperliquid")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("strategies", help="Listar estrategias disponibles").set_defaults(func=cmd_strategies)

    p = sub.add_parser("fetch", help="Descargar y cachear velas")
    add_data_args(p)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("backtest", help="Backtest de una estrategia")
    p.add_argument("--strategy", "-s", required=True)
    p.add_argument("--param", "-p", action="append", help="clave=valor (repetible)")
    p.add_argument("--out", help="Directorio donde guardar equity/trades/métricas")
    add_data_args(p)
    add_bt_args(p)
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("optimize", help="Grid search / walk-forward de parámetros")
    p.add_argument("--strategy", "-s", required=True)
    p.add_argument("--grid", "-g", action="append", help="clave=v1,v2,v3 (repetible)")
    p.add_argument("--param", "-p", action="append", help="Parámetros fijos clave=valor")
    p.add_argument("--metric", default="sharpe")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--min-trades", type=int, default=5)
    p.add_argument("--walk-forward", type=int, metavar="SPLITS", help="Validación walk-forward con N ventanas")
    p.add_argument("--train-frac", type=float, default=0.7)
    add_data_args(p)
    add_bt_args(p)
    p.set_defaults(func=cmd_optimize)

    p = sub.add_parser("run", help="Ejecutar una estrategia en paper o live")
    p.add_argument("--config", "-c", help="YAML con strategy/runner/risk")
    p.add_argument("--strategy", "-s")
    p.add_argument("--param", "-p", action="append")
    p.add_argument("--mode", choices=["paper", "live"])
    p.add_argument("--coin")
    p.add_argument("--interval")
    p.add_argument("--leverage", type=float)
    p.add_argument("--stop-loss", type=float)
    p.add_argument("--take-profit", type=float)
    p.add_argument("--poll-seconds", type=float)
    p.add_argument("--equity", type=float, default=10_000, help="Capital inicial en modo paper")
    p.add_argument("--mainnet", action="store_true", help="Usar mainnet (por defecto testnet)")
    p.add_argument("--i-understand-the-risks", action="store_true")
    p.add_argument("--replay", metavar="CSV", help="Paper trading acelerado sobre un CSV histórico")
    p.add_argument("--max-steps", type=int)
    p.set_defaults(func=cmd_run)
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args.func(args)


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])
