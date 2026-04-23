"""실거래 CLI."""
from __future__ import annotations
import argparse, logging, signal, sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import load_config
from src.core.logger import setup_logger
from src.core.alert import AlertManager
from src.api.rest_client import BybitRestClient
from src.api.ws_client import BybitWebSocketClient
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.execution.live_broker import LiveBroker
from src.strategies.registry import StrategyRegistry

logger = logging.getLogger(__name__)


def build_parser():
    parser = argparse.ArgumentParser(prog="main_live.py", description="Bybit 실거래")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("start", help="실거래 시작")
    p.add_argument("--strategies", nargs="+", required=True)
    p.add_argument("--mode", choices=["demo", "live"], default="demo")
    p.add_argument("--symbols", nargs="+")
    sub.add_parser("status", help="상태 조회")
    sub.add_parser("stop", help="안전 종료")
    return parser


def _handle_interactive(cmd, broker):
    parts = cmd.split()
    action = parts[0].lower()
    if action == "status":
        port = broker.get_portfolio()
        print(f"  equity: {port.equity:,.2f} | daily_pnl: {port.daily_pnl:+,.2f} | positions: {len(port.positions)}")
        for p in port.positions:
            print(f"    {p.symbol} {p.side} qty={p.qty} entry={p.entry_price:.2f} uPnL={p.unrealized_pnl:+.2f}")
    elif action == "buy" and len(parts) >= 3:
        sym, qty = parts[1], float(parts[2])
        sl = float(parts[parts.index("--sl")+1]) if "--sl" in parts else None
        tp = float(parts[parts.index("--tp")+1]) if "--tp" in parts else None
        print(f"  buy: {broker.manual_buy(sym, qty, sl, tp, 'CLI buy')}")
    elif action == "sell" and len(parts) >= 3:
        sym, qty = parts[1], float(parts[2])
        sl = float(parts[parts.index("--sl")+1]) if "--sl" in parts else None
        tp = float(parts[parts.index("--tp")+1]) if "--tp" in parts else None
        print(f"  sell: {broker.manual_sell(sym, qty, sl, tp, 'CLI sell')}")
    elif action == "close" and len(parts) >= 2:
        print(f"  close: {broker.manual_close(parts[1], 'CLI close')}")
    elif action == "close-all":
        print(f"  closed: {len(broker.manual_close_all('CLI close-all'))}")
    elif action == "update-sl" and len(parts) >= 3:
        broker.manual_update_stop(parts[1], float(parts[2]))
        print(f"  SL: {parts[1]} -> {parts[2]}")
    elif action == "update-tp" and len(parts) >= 3:
        broker.manual_update_tp(parts[1], float(parts[2]))
        print(f"  TP: {parts[1]} -> {parts[2]}")
    elif action == "help":
        for h in ["buy SYM QTY [--sl P] [--tp P]", "sell SYM QTY [--sl P] [--tp P]",
                   "close SYM", "close-all", "update-sl SYM P", "update-tp SYM P", "status"]:
            print(f"  {h}")
    else:
        print(f"  unknown: {cmd}")


def cmd_start(args, config):
    if args.mode == "live":
        config.app.base_url = "https://api.bybit.com"
        print("*** LIVE MODE ***")
    else:
        print("*** DEMO MODE ***")
    rest = BybitRestClient(config.app.api_key, config.app.api_secret, config.app.base_url)
    alert = AlertManager(config.alert)
    db = DBManager(str(PROJECT_ROOT / config.app.db_path), str(PROJECT_ROOT / "db/schema.sql"))
    db.initialize()
    broker = LiveBroker(rest, alert, leverage=config.app.leverage, initial_capital=50000.0)
    registry = StrategyRegistry()
    strategies = []
    for name in args.strategies:
        try:
            strategies.append(registry.get(name))
            print(f"  loaded: {name}")
        except KeyError:
            print(f"  not registered: {name}")
    ws = BybitWebSocketClient(ws_url=config.app.ws_url)
    symbols = args.symbols or ["BTCUSDT"]

    def on_kline(symbol, interval, kline):
        from src.core.types import Bar
        tf = f"{int(interval)}m" if interval.isdigit() else interval
        bar = Bar(symbol, int(kline["start"]), tf,
                  float(kline["open"]), float(kline["high"]),
                  float(kline["low"]), float(kline["close"]), float(kline["volume"]))
        for s in strategies:
            try:
                feed = HistoricalDataFeed(db=db, symbols=[symbol], timeframe=bar.timeframe)
                series = feed.get_history(symbol, s.warmup_bars)
                s.on_bar(bar, series, broker)
            except Exception as e:
                logger.error("%s error: %s", s.name, e)

    ws.on_kline_closed = on_kline
    alert.on_system_event(f"Start: {args.mode}, strategies={args.strategies}")
    print(f"\nTrading started ({args.mode}): {symbols}")
    print("Commands: buy/sell/close/close-all/update-sl/update-tp/status/help | Ctrl+C to exit\n")
    ws.start(symbols, ["60"])

    try:
        while True:
            try:
                cmd = input("> ").strip()
                if cmd:
                    _handle_interactive(cmd, broker)
            except EOFError:
                break
    except KeyboardInterrupt:
        pass
    finally:
        ws.stop()
        alert.on_system_event("Stop")
        print("\nTrading stopped")


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    config = load_config(str(PROJECT_ROOT / "config.yaml"))
    setup_logger(str(PROJECT_ROOT / "logs"), config.app.log_level)
    if args.command == "start":
        cmd_start(args, config)
    elif args.command == "status":
        print("Use within running process")
    elif args.command == "stop":
        print("Use Ctrl+C in running process")


if __name__ == "__main__":
    main()
