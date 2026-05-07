"""Generalized OHLCV history backfill — any timeframe, any symbol list.

Builds on the same paginated-fetch pattern as ``collect_5m_history.py`` and
``collect_daily_history.py`` but takes ``--timeframes`` + ``--symbols``
(or ``--top N`` for Bybit live ticker ranking) so one CLI invocation can
fill multiple (symbol, tf) gaps. Writes to the project's SQLite via
``DBManager.upsert_ohlcv`` — upsert is idempotent so re-runs are safe.

Selection (when ``--top N`` instead of explicit ``--symbols``):

- Bybit v5 ``GET /v5/market/tickers?category=linear`` is queried once.
- Symbols matching ``^[A-Z0-9]+USDT$`` (USDT-margined linear perpetuals,
  no futures with date suffix, no USDC) are sorted by ``turnover24h``
  descending and the top ``N`` are taken.

Usage::

    # Top 30 by turnover, 5 intraday timeframes, last 1 year.
    python -m scripts.collect_history --top 30 \
        --timeframes 5m 15m 30m 1h 4h --days 365

    # Explicit list, just 30m.
    python -m scripts.collect_history --symbols BTCUSDT ETHUSDT --timeframes 30m

    # Different end date for back-testing reproducibility.
    python -m scripts.collect_history --top 30 --timeframes 5m \
        --start 2025-01-01 --end 2025-12-31

Each (symbol, tf) is handled independently — failures don't stop the
remaining combinations. A summary table is printed at the end.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pybit.unified_trading import HTTP

from src.core.config import load_config
from src.data_manager.db import DBManager

UTC = timezone.utc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("collect_history")

BYBIT_KLINE_LIMIT = 1000

# Bybit v5 interval encoding (matches DBManager TIMEFRAME_TABLE keys).
_INTERVAL_CODE: dict[str, str] = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "4h": "240",
    "1d": "D",
}

# Approximate ms per bar for cursor stepping.
_BAR_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

_PERP_SYMBOL_RE = re.compile(r"^[A-Z0-9]+USDT$")


def _to_ms(iso_date: str) -> int:
    dt = datetime.strptime(iso_date, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def _fetch_top_n_by_turnover(n: int) -> list[str]:
    """Bybit v5 tickers (linear) → USDT-perp filter → top N by 24h turnover."""
    url = (
        "https://api.bybit.com/v5/market/tickers?"
        + urllib.parse.urlencode({"category": "linear"})
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": "collect_history/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    if data.get("retCode") != 0:
        raise RuntimeError(f"tickers retCode={data.get('retCode')}")
    items = data.get("result", {}).get("list", []) or []
    cands: list[tuple[str, float]] = []
    for it in items:
        sym = it.get("symbol") or ""
        if not _PERP_SYMBOL_RE.match(sym):
            continue
        try:
            turnover = float(it.get("turnover24h") or 0)
        except (TypeError, ValueError):
            continue
        cands.append((sym, turnover))
    cands.sort(key=lambda r: r[1], reverse=True)
    return [s for s, _ in cands[:n]]


def _parse_kline_list(items: list[list[str]], symbol: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        if len(item) < 6:
            continue
        rows.append(
            {
                "symbol": symbol,
                "open_time": int(item[0]),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
                "turnover": float(item[6]) if len(item) > 6 else None,
            }
        )
    return rows


def fetch_range(
    http: HTTP,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    *,
    retries: int = 3,
    pause_s: float = 0.20,
) -> list[dict[str, Any]]:
    """Page through Bybit kline newest-first until ``[start_ms, end_ms]`` covered.

    Cursor walks ``end`` backward each iteration to ``oldest_received - 1ms``.
    Identical contract to ``collect_5m_history.fetch_5m_range`` but tf-agnostic.
    """
    interval_code = _INTERVAL_CODE[timeframe]
    bar_ms = _BAR_MS[timeframe]
    page_span_ms = BYBIT_KLINE_LIMIT * bar_ms

    all_rows: list[dict[str, Any]] = []
    cursor_end = end_ms
    pages = 0
    while cursor_end > start_ms:
        page_start = max(start_ms, cursor_end - page_span_ms)
        for attempt in range(retries):
            try:
                resp = http.get_kline(
                    category="linear",
                    symbol=symbol,
                    interval=interval_code,
                    start=page_start,
                    end=cursor_end,
                    limit=BYBIT_KLINE_LIMIT,
                )
                if resp.get("retCode") != 0:
                    raise RuntimeError(
                        f"retCode={resp.get('retCode')} "
                        f"msg={resp.get('retMsg')}"
                    )
                items = resp.get("result", {}).get("list", []) or []
                break
            except Exception as exc:
                if attempt == retries - 1:
                    raise
                logger.warning(
                    "    retry %d/%d for %s/%s: %s",
                    attempt + 1, retries, symbol, timeframe, exc,
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
        new_cursor = oldest_ms - bar_ms
        if new_cursor >= cursor_end:
            break
        cursor_end = new_cursor
        time.sleep(pause_s)
    logger.info("    pages=%d", pages)
    # dedup + clip to range.
    dedup: dict[int, dict[str, Any]] = {}
    for r in all_rows:
        dedup[r["open_time"]] = r
    out = sorted(dedup.values(), key=lambda r: r["open_time"])
    out = [r for r in out if start_ms <= r["open_time"] <= end_ms]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Bybit OHLCV across N symbols × M timeframes.",
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--symbols",
        nargs="*",
        help="explicit symbol list (e.g. BTCUSDT ETHUSDT SOLUSDT)",
    )
    g.add_argument(
        "--top",
        type=int,
        help="auto-select top N USDT-perp by 24h turnover via Bybit live",
    )
    parser.add_argument(
        "--timeframes",
        nargs="+",
        required=True,
        choices=sorted(_INTERVAL_CODE),
        help="any subset of 1m / 5m / 15m / 30m / 1h / 4h / 1d",
    )
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="explicit YYYY-MM-DD; overrides --days when set",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="explicit YYYY-MM-DD (inclusive); default = today UTC",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch but skip DB upsert",
    )
    args = parser.parse_args()

    now = datetime.now(UTC)
    if args.end:
        end_ms = _to_ms(args.end) + 86_400_000 - 1
    else:
        end_ms = int(now.timestamp() * 1000)
    if args.start:
        start_ms = _to_ms(args.start)
    else:
        start_ms = end_ms - args.days * 86_400_000

    if args.symbols:
        symbols = list(args.symbols)
    else:
        logger.info("fetching top %d by 24h turnover ...", args.top)
        symbols = _fetch_top_n_by_turnover(args.top)
        for i, s in enumerate(symbols, start=1):
            logger.info("  %2d. %s", i, s)

    cfg = load_config()
    db = DBManager(str(PROJECT_ROOT / cfg.app.db_path))
    db.initialize()  # create any missing ohlcv_<tf> tables
    http = HTTP()  # mainnet, no auth needed for klines

    summary: list[dict[str, Any]] = []
    for tf in args.timeframes:
        logger.info("=== timeframe %s (window %s..%s) ===",
                    tf, _from_ms(start_ms), _from_ms(end_ms))
        for sym in symbols:
            logger.info("  %s ...", sym)
            row: dict[str, Any] = {"symbol": sym, "timeframe": tf}
            try:
                rows = fetch_range(http, sym, tf, start_ms, end_ms)
            except Exception as e:  # noqa: BLE001 — keep grid going
                logger.error("  FAILED %s/%s: %s", sym, tf, e)
                row["status"] = "fetch_failed"
                row["error"] = f"{type(e).__name__}: {e}"
                summary.append(row)
                continue
            row["fetched"] = len(rows)
            if rows:
                row["first"] = _from_ms(rows[0]["open_time"])
                row["last"] = _from_ms(rows[-1]["open_time"])
            if not rows:
                logger.info("    empty")
                row["status"] = "empty"
                summary.append(row)
                continue
            if args.dry_run:
                row["status"] = "dry_run"
                summary.append(row)
                continue
            inserted = db.upsert_ohlcv(symbol=sym, timeframe=tf, rows=rows)
            post_count = db.get_bar_count(sym, tf)
            row["inserted"] = inserted
            row["db_count"] = post_count
            row["status"] = "ok"
            logger.info(
                "    fetched=%d inserted=%d db_count=%d range=%s..%s",
                len(rows), inserted, post_count, row["first"], row["last"],
            )
            summary.append(row)

    print()
    print("=== Summary ===")
    print(f"{'symbol':<14} {'tf':<5} {'status':<14} {'fetched':>8} {'first':<18} {'last':<18}")
    for r in summary:
        print(
            f"{r['symbol']:<14} {r['timeframe']:<5} {r.get('status','-'):<14} "
            f"{r.get('fetched', 0):>8} "
            f"{r.get('first','-'):<18} {r.get('last','-'):<18}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
