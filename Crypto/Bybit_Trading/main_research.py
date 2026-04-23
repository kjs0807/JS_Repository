"""리서치/백테스트 CLI."""
from __future__ import annotations
import argparse, sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import load_config
from src.core.logger import setup_logger
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.strategies.registry import StrategyRegistry
from src.backtester.engine import BacktestEngine
from src.backtester.analyzer import PerformanceAnalyzer
from src.backtester.explorer import StrategyExplorer


def build_parser():
    parser = argparse.ArgumentParser(prog="main_research.py", description="Bybit 전략 리서치")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("backtest", help="단일 백테스트")
    p.add_argument("--strategy", required=True); p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--tf", default="1h"); p.add_argument("--start"); p.add_argument("--end")
    p = sub.add_parser("explore", help="전략 탐색")
    p.add_argument("--tf", nargs="+", default=["1h"]); p.add_argument("--universe", default="top30")
    p = sub.add_parser("walkforward", help="Walk-Forward")
    p.add_argument("--strategy", required=True); p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--tf", default="1h")
    p = sub.add_parser("overfit", help="오버피팅 검증")
    p.add_argument("--strategy", required=True); p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--tf", default="1h")
    p = sub.add_parser("compare", help="전략 비교")
    p.add_argument("--strategies", nargs="+", required=True)
    p.add_argument("--symbol", default="BTCUSDT"); p.add_argument("--tf", default="1h")
    return parser


def cmd_backtest(args, db, registry, config):
    strategy = registry.get(args.strategy)
    feed = HistoricalDataFeed(db=db, symbols=[args.symbol], timeframe=args.tf)
    result = BacktestEngine().run(strategy, feed, config.backtest, symbol=args.symbol)
    print(PerformanceAnalyzer().generate_report(result))


def cmd_explore(args, db, registry, config):
    feed = HistoricalDataFeed(db=db, symbols=["BTCUSDT"], timeframe=args.tf[0])
    report = StrategyExplorer().explore(registry, feed, config.backtest, ["BTCUSDT"])
    print(report.summary())


def cmd_compare(args, db, registry, config):
    engine, analyzer, results = BacktestEngine(), PerformanceAnalyzer(), []
    for name in args.strategies:
        feed = HistoricalDataFeed(db=db, symbols=[args.symbol], timeframe=args.tf)
        results.append(engine.run(registry.get(name), feed, config.backtest, args.symbol))
    for row in analyzer.compare(results):
        print(f"{row['strategy_name']}: PnL={row['total_pnl']:+,.0f} Sharpe={row['sharpe_ratio']:.3f}")


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.command: parser.print_help(); sys.exit(0)
    config = load_config(str(PROJECT_ROOT / "config.yaml"))
    setup_logger(str(PROJECT_ROOT / "logs"), config.app.log_level)
    db = DBManager(str(PROJECT_ROOT / config.app.db_path), str(PROJECT_ROOT / "db/schema.sql"))
    db.initialize()
    registry = StrategyRegistry()
    {"backtest": lambda: cmd_backtest(args, db, registry, config),
     "explore": lambda: cmd_explore(args, db, registry, config),
     "compare": lambda: cmd_compare(args, db, registry, config)
    }.get(args.command, lambda: print(f"미구현: {args.command}"))()

if __name__ == "__main__":
    main()
