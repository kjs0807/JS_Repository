"""Gap filler — backfill missing OHLCV bars from Bybit mainnet.

Inspired by ``_legacy/collector/historical.py`` (pagination pattern) but
adapted to the current ``src/`` architecture:
- Uses ``DBManager.upsert_bars(symbol, timeframe, rows)`` from
  ``src/data_manager/db.py`` so writes go through the canonical alias
  map (``1h`` → ``ohlcv_1h``, ``1d`` → ``ohlcv_daily``).
- Uses a plain ``pybit.unified_trading.HTTP()`` (mainnet, no auth) for
  kline reads. The live trading config points at ``api-demo.bybit.com``
  which has unreliable historical data for old windows.
- Accepts any interval ("15", "60", "240", "D"), any [start, now]
  range, and pages in 1000-bar chunks with a short sleep between calls.

The point of making this a module rather than a script-local helper is
so both the daily collector (``scripts/collect_daily_history.py``) and
the live paper runner (``scripts/run_bbkc_paper_live.py``) share one
proven implementation. The daily collector can keep its existing
self-contained form — this module is an additive surface.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pybit.unified_trading import HTTP

from src.data_manager.db import DBManager

logger = logging.getLogger(__name__)

# Bybit v5 interval → ms mapping. Matches _legacy/collector/historical.py.
INTERVAL_MS: Dict[str, int] = {
    "1":    60_000,
    "3":    3 * 60_000,
    "5":    5 * 60_000,
    "15":   15 * 60_000,
    "30":   30 * 60_000,
    "60":   60 * 60_000,
    "120":  120 * 60_000,
    "240":  240 * 60_000,
    "360":  360 * 60_000,
    "720":  720 * 60_000,
    "D":    86_400_000,
    "W":    7 * 86_400_000,
}

# Bybit interval → DBManager timeframe alias.
INTERVAL_TF: Dict[str, str] = {
    "15":  "15m",
    "60":  "1h",
    "240": "4h",
    "D":   "1d",
}

BYBIT_KLINE_LIMIT = 1000


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


def fetch_kline_range(
    http: HTTP,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    retries: int = 3,
    pause_s: float = 0.15,
) -> List[Dict[str, Any]]:
    """Page through Bybit v5 kline API and return all bars in
    [start_ms, end_ms], sorted ascending, deduplicated.

    Bybit returns newest-first, up to 1000 per call. We iterate from
    ``end_ms`` backwards using ``cursor_end`` until we reach
    ``start_ms`` or the result page is empty. Each page is appended
    to ``all_rows`` and at the end we de-dup on ``open_time`` and
    sort.
    """
    interval_ms = INTERVAL_MS.get(interval)
    if interval_ms is None:
        raise ValueError(f"Unsupported interval: {interval}")
    all_rows: List[Dict[str, Any]] = []
    cursor_end = end_ms
    while cursor_end > start_ms:
        for attempt in range(retries):
            try:
                resp = http.get_kline(
                    category="linear",
                    symbol=symbol,
                    interval=interval,
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
                    "[gap_filler] retry %d/%d for %s %s: %s",
                    attempt + 1, retries, symbol, interval, exc,
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
        new_cursor = oldest_ms - interval_ms
        if new_cursor >= cursor_end:
            break
        cursor_end = new_cursor
        time.sleep(pause_s)
    # De-dup + sort + range-trim
    dedup: Dict[int, Dict[str, Any]] = {}
    for r in all_rows:
        dedup[r["open_time"]] = r
    out = sorted(dedup.values(), key=lambda r: r["open_time"])
    out = [r for r in out if start_ms <= r["open_time"] <= end_ms]
    return out


def fill_gap(
    db: DBManager,
    symbol: str,
    interval: str,
    since_ms: int,
    until_ms: Optional[int] = None,
    http: Optional[HTTP] = None,
) -> int:
    """Backfill ``[since_ms, until_ms or now]`` for ``symbol`` at
    ``interval``. Returns the number of newly inserted rows.

    Idempotent — underlying ``DBManager.upsert_bars`` uses INSERT OR
    REPLACE so re-running on an already-full range is safe.
    """
    if http is None:
        http = HTTP()  # mainnet, no auth
    if until_ms is None:
        until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    tf = INTERVAL_TF.get(interval)
    if tf is None:
        raise ValueError(f"interval {interval} has no DB timeframe alias")
    rows = fetch_kline_range(http, symbol, interval, since_ms, until_ms)
    if not rows:
        return 0
    return int(db.upsert_bars(symbol, tf, rows))


def fill_gap_for_universe(
    db: DBManager,
    symbols: List[str],
    interval: str,
    since_ms: int,
    until_ms: Optional[int] = None,
) -> Dict[str, int]:
    """Run ``fill_gap`` for every symbol and return per-symbol counts."""
    http = HTTP()
    out: Dict[str, int] = {}
    for sym in symbols:
        try:
            n = fill_gap(db, sym, interval, since_ms, until_ms, http=http)
        except Exception as exc:
            logger.error("[gap_filler] %s %s failed: %s", sym, interval, exc)
            n = -1
        out[sym] = n
    return out


def current_db_tail_ms(
    db: DBManager, symbol: str, interval: str,
) -> Optional[int]:
    """Return the last open_time currently stored for ``symbol`` at
    ``interval``, or ``None`` if the table is empty for that symbol.
    """
    tf = INTERVAL_TF.get(interval)
    if tf is None:
        raise ValueError(f"interval {interval} has no DB timeframe alias")
    start, end = db.get_bar_range(symbol, tf)
    return end


__all__ = [
    "INTERVAL_MS",
    "INTERVAL_TF",
    "fetch_kline_range",
    "fill_gap",
    "fill_gap_for_universe",
    "current_db_tail_ms",
]
