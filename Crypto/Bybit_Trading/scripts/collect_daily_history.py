"""Backfill daily (1D) OHLCV from Bybit mainnet into `ohlcv_daily`.

Context
-------
The RSI-divergence regime research track needs multi-year daily data
(2021-01-01 onwards) covering every screenshot case in
`docs/Screenshot/`. The existing DB has daily bars starting
2024-02-04 (BTC/ETH/SOL) or 2025-03-01 (LINK/AVAX), which is not
enough. This script backfills ``ohlcv_daily`` directly from Bybit v5
public kline endpoint.

Why mainnet (not demo)
----------------------
The operational config points at ``api-demo.bybit.com`` (paper
trading). Demo env historical kline data is unreliable for old
windows, so this script bypasses the config's base_url and uses the
plain ``pybit.HTTP()`` constructor which hits the public mainnet
endpoint for market data. No API key needed for klines.

Writes
------
Uses ``DBManager.upsert_bars(symbol, timeframe="1d", rows=...)`` which
resolves to the ``ohlcv_daily`` table via the alias map in
``db.py``. Upsert is idempotent so re-running is safe.

Usage
-----
python -m scripts.collect_daily_history
python -m scripts.collect_daily_history --symbols BTCUSDT ETHUSDT
python -m scripts.collect_daily_history --start 2021-01-01 --end 2026-04-15
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pybit.unified_trading import HTTP

from src.core.config import load_config
from src.data_manager.db import DBManager

logger = logging.getLogger(__name__)

DAY_MS = 86_400_000
BYBIT_KLINE_LIMIT = 1000  # v5 max per call


def _to_ms(iso_date: str) -> int:
    dt = datetime.strptime(iso_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _parse_kline_list(
    items: List[List[str]], symbol: str,
) -> List[Dict[str, Any]]:
    """Bybit returns list ordered newest-first. We normalize to the
    dict shape ``DBManager.upsert_bars`` expects and preserve raw
    values as floats."""
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


def fetch_daily_range(
    http: HTTP, symbol: str, start_ms: int, end_ms: int,
    retries: int = 3, pause_s: float = 0.25,
) -> List[Dict[str, Any]]:
    """Page through Bybit kline API until we cover ``[start_ms, end_ms]``.

    Bybit returns at most 1000 bars per call. 1000 days ≈ 2.74 years,
    so for a 5-year window we need ~2-3 pages per symbol. We iterate
    from ``end_ms`` backwards (API returns newest-first) and prepend
    each page to the result.
    """
    all_rows: List[Dict[str, Any]] = []
    cursor_end = end_ms
    while cursor_end > start_ms:
        # Ask for bars ending at cursor_end; Bybit will return up to
        # 1000 bars before it. If we ask start=start_ms + limit*DAY_MS
        # explicitly, old bars return correctly.
        for attempt in range(retries):
            try:
                resp = http.get_kline(
                    category="linear",
                    symbol=symbol,
                    interval="D",
                    start=start_ms,
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
        oldest_ms = rows[0]["open_time"]
        if oldest_ms <= start_ms:
            break
        # Step the cursor back so the next page ends just before this
        # page's oldest bar.
        new_cursor = oldest_ms - DAY_MS
        if new_cursor >= cursor_end:
            break
        cursor_end = new_cursor
        time.sleep(pause_s)
    # De-duplicate on open_time and sort ascending.
    dedup: Dict[int, Dict[str, Any]] = {}
    for r in all_rows:
        dedup[r["open_time"]] = r
    out = sorted(dedup.values(), key=lambda r: r["open_time"])
    # Trim to the requested range just in case Bybit returned extras.
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
        description="Backfill daily OHLCV from Bybit mainnet.",
    )
    parser.add_argument(
        "--symbols", nargs="*",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"],
    )
    parser.add_argument("--start", type=str, default="2021-01-01")
    parser.add_argument(
        "--end", type=str, default=None,
        help="Inclusive end date, default = today UTC",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    start_ms = _to_ms(args.start)
    if args.end:
        end_ms = _to_ms(args.end) + DAY_MS - 1
    else:
        now = datetime.now(timezone.utc)
        end_ms = int(now.timestamp() * 1000)

    cfg = load_config()
    db = DBManager(cfg.app.db_path)
    http = HTTP()  # mainnet, no auth needed for klines

    logger.info(
        "Backfill daily OHLCV: symbols=%s range=%s..%s",
        args.symbols, _from_ms(start_ms), _from_ms(end_ms),
    )

    summary: Dict[str, Dict[str, Any]] = {}
    for sym in args.symbols:
        logger.info("fetching %s ...", sym)
        try:
            rows = fetch_daily_range(http, sym, start_ms, end_ms)
        except Exception as exc:
            logger.error("fetch failed for %s: %s", sym, exc)
            summary[sym] = {"ok": False, "error": str(exc)}
            continue
        logger.info("  fetched: %s", _fmt_row_range(rows))
        if args.dry_run:
            summary[sym] = {"ok": True, "rows": len(rows), "dry_run": True}
            continue
        inserted = db.upsert_bars(sym, "1d", rows)
        # Post-check: ask DB for its current row count + coverage
        post_count = db.get_bar_count(sym, "1d")
        post_range = db.get_bar_range(sym, "1d")
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
            f"  {sym:10s} fetched={s['fetched']:5d} inserted={s['inserted']:5d} "
            f"db_total={s['db_count']:5d} range={s['db_first']}..{s['db_last']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
