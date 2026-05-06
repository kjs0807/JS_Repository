"""Backfill 5-minute OHLCV from Bybit mainnet into ``ohlcv_5m``.

Mirrors the design of ``scripts.collect_daily_history`` but for the 5m
timeframe. Uses the public mainnet kline endpoint (no auth needed).

Usage
-----
python -m scripts.collect_5m_history
python -m scripts.collect_5m_history --symbols BTCUSDT ETHUSDT --days 730
python -m scripts.collect_5m_history --start 2024-05-04 --end 2026-05-04
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pybit.unified_trading import HTTP

from src.core.config import load_config
from src.data_manager.db import DBManager

logger = logging.getLogger(__name__)

MIN_MS = 60_000
BAR_MS = 5 * MIN_MS                    # 5m bar = 300_000 ms
BYBIT_KLINE_LIMIT = 1000               # v5 max per call
PAGE_SPAN_MS = BYBIT_KLINE_LIMIT * BAR_MS  # ~3.47 days per page


def _to_ms(iso_date: str) -> int:
    dt = datetime.strptime(iso_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime(
        "%Y-%m-%d %H:%M"
    )


def _parse_kline_list(
    items: List[List[str]], symbol: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in items:
        if len(item) < 6:
            continue
        rows.append({
            "symbol": symbol,
            "open_time": int(item[0]),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
            "turnover": float(item[6]) if len(item) > 6 else None,
        })
    return rows


def fetch_5m_range(
    http: HTTP, symbol: str, start_ms: int, end_ms: int,
    retries: int = 3, pause_s: float = 0.25,
) -> List[Dict[str, Any]]:
    """Page through Bybit 5m kline API until ``[start_ms, end_ms]`` is covered.

    Bybit returns up to 1000 bars per call (newest-first). We start from
    ``end_ms`` and walk the cursor backwards page by page.
    """
    all_rows: List[Dict[str, Any]] = []
    cursor_end = end_ms
    pages = 0
    while cursor_end > start_ms:
        # Each page covers up to PAGE_SPAN_MS before cursor_end.
        page_start = max(start_ms, cursor_end - PAGE_SPAN_MS)
        for attempt in range(retries):
            try:
                resp = http.get_kline(
                    category="linear",
                    symbol=symbol,
                    interval="5",
                    start=page_start,
                    end=cursor_end,
                    limit=BYBIT_KLINE_LIMIT,
                )
                if resp.get("retCode") != 0:
                    raise RuntimeError(
                        f"retCode={resp.get('retCode')} "
                        f"msg={resp.get('retMsg')}"
                    )
                items = resp.get("result", {}).get("list", [])
                break
            except Exception as exc:
                if attempt == retries - 1:
                    raise
                logger.warning(
                    "fetch retry %d/%d for %s: %s",
                    attempt + 1, retries, symbol, exc,
                )
                time.sleep(1.0 * (attempt + 1))
        if not items:
            break
        rows = _parse_kline_list(items, symbol)
        rows.sort(key=lambda r: r["open_time"])
        all_rows.extend(rows)
        pages += 1
        oldest_ms = rows[0]["open_time"]
        if oldest_ms <= start_ms:
            break
        new_cursor = oldest_ms - BAR_MS
        if new_cursor >= cursor_end:
            break
        cursor_end = new_cursor
        time.sleep(pause_s)
    logger.info("  pages=%d", pages)
    dedup: Dict[int, Dict[str, Any]] = {}
    for r in all_rows:
        dedup[r["open_time"]] = r
    out = sorted(dedup.values(), key=lambda r: r["open_time"])
    out = [r for r in out if start_ms <= r["open_time"] <= end_ms]
    return out


def _fmt_row_range(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<empty>"
    return (
        f"n={len(rows)} first={_from_ms(rows[0]['open_time'])} "
        f"last={_from_ms(rows[-1]['open_time'])}"
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Backfill 5m OHLCV from Bybit mainnet.",
    )
    parser.add_argument(
        "--symbols", nargs="*", default=["BTCUSDT", "ETHUSDT"],
    )
    parser.add_argument(
        "--days", type=int, default=730,
        help="기본: 2년치 (730일). --start/--end 사용 시 무시됨.",
    )
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument(
        "--end", type=str, default=None,
        help="Inclusive end date. 기본: 현재 시각 (UTC).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    if args.end:
        end_ms = _to_ms(args.end) + 86_400_000 - 1
    else:
        end_ms = int(now.timestamp() * 1000)
    if args.start:
        start_ms = _to_ms(args.start)
    else:
        start_dt = now - timedelta(days=args.days)
        start_ms = int(start_dt.timestamp() * 1000)

    cfg = load_config()
    db = DBManager(cfg.app.db_path)
    db.initialize()  # ohlcv_5m 테이블이 없으면 생성

    http = HTTP()  # mainnet, no auth needed for klines

    logger.info(
        "Backfill 5m OHLCV: symbols=%s range=%s..%s",
        args.symbols, _from_ms(start_ms), _from_ms(end_ms),
    )

    summary: Dict[str, Dict[str, Any]] = {}
    for sym in args.symbols:
        logger.info("fetching %s ...", sym)
        try:
            rows = fetch_5m_range(http, sym, start_ms, end_ms)
        except Exception as exc:
            logger.error("fetch failed for %s: %s", sym, exc)
            summary[sym] = {"ok": False, "error": str(exc)}
            continue
        logger.info("  fetched: %s", _fmt_row_range(rows))
        if args.dry_run:
            summary[sym] = {"ok": True, "rows": len(rows), "dry_run": True}
            continue
        inserted = db.upsert_bars(sym, "5m", rows)
        post_count = db.get_bar_count(sym, "5m")
        post_range = db.get_bar_range(sym, "5m")
        first_s = _from_ms(post_range[0]) if post_range[0] else None
        last_s = _from_ms(post_range[1]) if post_range[1] else None
        summary[sym] = {
            "ok": True,
            "fetched": len(rows),
            "inserted": inserted,
            "db_count": post_count,
            "db_first": first_s,
            "db_last": last_s,
        }
        logger.info(
            "  upsert: inserted=%d db_count=%d range=%s..%s",
            inserted, post_count, first_s, last_s,
        )

    print()
    print("=== Summary ===")
    for sym, s in summary.items():
        if not s.get("ok"):
            print(f"  {sym:10s} FAIL: {s.get('error')}")
            continue
        if s.get("dry_run"):
            print(f"  {sym:10s} DRY_RUN fetched={s['rows']}")
            continue
        print(
            f"  {sym:10s} fetched={s['fetched']:6d} "
            f"inserted={s['inserted']:6d} "
            f"db_total={s['db_count']:6d} "
            f"range={s['db_first']}..{s['db_last']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
