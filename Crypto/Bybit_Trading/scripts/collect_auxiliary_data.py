"""Funding Rate + Open Interest 데이터 수집 스크립트.

Strategy 5 (Funding MR) 및 Strategy 6 (OI Surge)용 과거 데이터를 Bybit REST에서 수집.

Usage:
    python scripts/collect_auxiliary_data.py funding --symbols BTCUSDT ETHUSDT --days 730
    python scripts/collect_auxiliary_data.py oi --symbols BTCUSDT --days 365 --interval 1h
    python scripts/collect_auxiliary_data.py all --days 730
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from src.core.config import load_config
from src.data_manager.db import DBManager
from src.api.rest_client import BybitRestClient


DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"]


def collect_funding(client: BybitRestClient, db: DBManager, symbols, days: int):
    """펀딩비 수집.

    Bybit funding rate는 8시간 주기. 2년 = 730일 × 3회/일 = 약 2190건 per symbol.
    API limit 200이므로 페이지네이션 필요.
    """
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000

    for symbol in symbols:
        logger.info(f"Collecting funding for {symbol} ({days}d)...")
        cur_end = end_ms
        total_saved = 0

        while cur_end > start_ms:
            rows = client.get_funding_history(symbol, limit=200, start=start_ms, end=cur_end)
            if not rows:
                logger.info(f"  No more data, stopping at {cur_end}")
                break

            saved = db.upsert_funding_rates(rows)
            total_saved += saved
            logger.info(f"  Page: got {len(rows)} rows, saved {saved}, total={total_saved}")

            # 페이지네이션: 가장 오래된 funding_time보다 이전으로
            oldest = min(r["funding_time"] for r in rows)
            if oldest <= start_ms:
                break
            cur_end = oldest - 1
            time.sleep(0.3)  # API rate limit

        logger.info(f"Done {symbol}: {total_saved} funding records")


def collect_oi(client: BybitRestClient, db: DBManager, symbols, days: int, interval: str = "1h"):
    """Open Interest 수집.

    Bybit OI는 interval_time에 따라 주기가 다름 (1h = 1h 간격).
    1h × 365d × 24 = 8760건 per symbol.
    """
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000

    for symbol in symbols:
        logger.info(f"Collecting OI for {symbol} ({days}d, {interval})...")
        cur_end = end_ms
        total_saved = 0

        while cur_end > start_ms:
            rows = client.get_open_interest_history(
                symbol, interval_time=interval, limit=200, start=start_ms, end=cur_end,
            )
            if not rows:
                logger.info(f"  No more data")
                break

            saved = db.upsert_open_interest(rows)
            total_saved += saved
            logger.info(f"  Page: got {len(rows)} rows, saved {saved}, total={total_saved}")

            oldest = min(r["timestamp"] for r in rows)
            if oldest <= start_ms:
                break
            cur_end = oldest - 1
            time.sleep(0.3)

        logger.info(f"Done {symbol}: {total_saved} OI records")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["funding", "oi", "all"])
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--interval", default="1h", help="OI interval (5min/15min/30min/1h/4h/1d)")
    args = parser.parse_args()

    config = load_config(str(PROJECT_ROOT / "config.yaml"))
    client = BybitRestClient(
        config.app.api_key, config.app.api_secret, config.app.base_url,
    )
    db = DBManager(
        db_path=str(PROJECT_ROOT / config.app.db_path),
        schema_path=str(PROJECT_ROOT / "db" / "schema.sql"),
    )
    db.initialize()

    if args.stage in ("funding", "all"):
        collect_funding(client, db, args.symbols, args.days)
    if args.stage in ("oi", "all"):
        collect_oi(client, db, args.symbols, args.days, args.interval)


if __name__ == "__main__":
    main()
