"""데이터 관리 CLI."""
from __future__ import annotations
import argparse, sys, time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import load_config
from src.core.logger import setup_logger
from src.data_manager.db import DBManager
from src.data_manager.universe import UniverseManager
from src.api.rest_client import BybitRestClient


def build_parser():
    parser = argparse.ArgumentParser(prog="main_data.py", description="Bybit 데이터 관리")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("collect", help="과거 OHLCV 수집")
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT"])
    p.add_argument("--tf", default="1h")
    p.add_argument("--days", type=int, default=365)
    p = sub.add_parser("fill-gaps", help="데이터 갭 보충")
    p.add_argument("--tf", default="1h")
    p = sub.add_parser("universe", help="유니버스 조회")
    p.add_argument("--top", type=int, default=30)
    p = sub.add_parser("info", help="저장 데이터 요약")
    p.add_argument("--symbol", required=True)
    p.add_argument("--tf", default="1h")
    return parser


def cmd_collect(args, db, config):
    client = BybitRestClient(config.app.api_key, config.app.api_secret, config.app.base_url)
    interval_map = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}
    interval = interval_map.get(args.tf, "60")
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - args.days * 86400000
    for symbol in args.symbols:
        print(f"수집: {symbol} {args.tf} ({args.days}일)")
        bars = client.get_klines(symbol, interval, limit=1000, start=start_ms, end=now_ms)
        if bars:
            db.upsert_bars(symbol, args.tf, bars)
            print(f"  저장: {len(bars)}봉")
        else:
            print(f"  데이터 없음")


def cmd_fillgaps(args, db, config):
    print(f"fill-gaps: {args.tf} (Collector.fill_gaps 연동 예정)")


def cmd_universe(args, config):
    client = BybitRestClient(config.app.api_key, config.app.api_secret, config.app.base_url)
    products = client.get_instruments()
    symbols = [p["symbol"] for p in products if p.get("quote_coin") == "USDT"]
    um = UniverseManager(config.data)
    universe = um.build(symbols[:args.top * 2])
    print(f"유니버스 ({len(universe)}개):")
    for i, s in enumerate(universe, 1):
        print(f"  {i:2d}. {s}")


def cmd_info(args, db):
    count = db.get_bar_count(args.symbol, args.tf)
    min_ts, max_ts = db.get_bar_range(args.symbol, args.tf)
    print(f"{args.symbol} ({args.tf}): {count:,}봉")
    if min_ts and max_ts:
        print(f"  {datetime.fromtimestamp(min_ts/1000, tz=timezone.utc):%Y-%m-%d} ~ "
              f"{datetime.fromtimestamp(max_ts/1000, tz=timezone.utc):%Y-%m-%d}")


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help(); sys.exit(0)
    config = load_config(str(PROJECT_ROOT / "config.yaml"))
    setup_logger(str(PROJECT_ROOT / "logs"), config.app.log_level)
    db = DBManager(str(PROJECT_ROOT / config.app.db_path), str(PROJECT_ROOT / "db/schema.sql"))
    db.initialize()
    {"collect": lambda: cmd_collect(args, db, config),
     "fill-gaps": lambda: cmd_fillgaps(args, db, config),
     "universe": lambda: cmd_universe(args, config),
     "info": lambda: cmd_info(args, db)}.get(args.command, lambda: None)()

if __name__ == "__main__":
    main()
