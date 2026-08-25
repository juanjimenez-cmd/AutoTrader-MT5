from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .backtest import Backtester, load_candles_csv
from .config import load_config
from .engine import AutoTrader
from .mt5_adapter import MT5Broker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autotrader-mt5", description="DEMO-only multi-asset MT5 bot")
    commands = parser.add_subparsers(dest="command", required=True)
    live = commands.add_parser("live", help="scan and trade a DEMO account")
    live.add_argument("--config", default="configs/autotrader.toml")
    live.add_argument("--once", action="store_true", help="run one scanner cycle")
    backtest = commands.add_parser("backtest", help="backtest the shared signal engine on M5 CSV data")
    backtest.add_argument("--config", default="configs/autotrader.toml")
    backtest.add_argument("--csv", required=True)
    backtest.add_argument("--symbol", required=True)
    backtest.add_argument("--initial-equity", type=float, default=10_000.0)
    backtest.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.command == "live":
        asyncio.run(AutoTrader(config, MT5Broker(config)).run(once=args.once))
        return 0
    candles = load_candles_csv(args.csv)
    report = Backtester(config, args.initial_equity).run(args.symbol, candles)
    rendered = report.to_json()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0
