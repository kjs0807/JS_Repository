"""Pick top-30 USDT-margined linear perp symbols by 24h turnover, prefetch
1-year 30m bars into a shared parquet cache.

Selection — Bybit v5 ``GET /v5/market/tickers?category=linear``:

- Filter symbols matching ``^[A-Z0-9]+USDT$`` (excludes futures with date
  suffix like ``BTC-25APR25`` and USDC-quoted ``BTCPERP``).
- Sort by ``turnover24h`` descending; keep the top 30.

Cache — :class:`BybitDataSource` writes ``{cache_dir}/{symbol}_30m.parquet``
incrementally. The downstream grid script can re-use the same cache by
pointing ``DataSourceConfig.base_dir`` at it. Symbols that don't have a
full year of history (recent listings) get however many bars Bybit returns
— the grid handles partial windows fine.

Usage::

    python -m scripts.fetch_top30_30m              # 1y default
    python -m scripts.fetch_top30_30m --days 180   # 6 months
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from backtester.data.bybit_source import BybitDataSource  # noqa: E402

UTC = timezone.utc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_top30")

BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"


def _fetch_tickers() -> list[dict[str, str]]:
    """Bybit v5 tickers — linear category. Returns the raw ``result.list``."""
    qs = urllib.parse.urlencode({"category": "linear"})
    url = f"{BYBIT_TICKERS_URL}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "sats-grid/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    if data.get("retCode") != 0:
        raise RuntimeError(
            f"Bybit tickers retCode={data.get('retCode')} msg={data.get('retMsg')}"
        )
    return list(data.get("result", {}).get("list", []) or [])


def _select_top_n(tickers: list[dict[str, str]], n: int) -> list[tuple[str, float]]:
    """Filter to USDT-margined perpetuals, sort by turnover24h desc, top N."""
    candidates: list[tuple[str, float]] = []
    for t in tickers:
        sym = t.get("symbol") or ""
        # Perp = no dash (futures have date suffix). USDT-margined.
        if "-" in sym or not sym.endswith("USDT"):
            continue
        try:
            turnover = float(t.get("turnover24h") or 0)
        except (TypeError, ValueError):
            continue
        candidates.append((sym, turnover))
    candidates.sort(key=lambda r: r[1], reverse=True)
    return candidates[:n]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    if args.end:
        end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    else:
        now = datetime.now(UTC)
        end = datetime(now.year, now.month, now.day, tzinfo=UTC)
    start = end - timedelta(days=args.days)

    if args.output:
        cache_dir = Path(args.output)
    else:
        cache_dir = (
            PROJECT_ROOT
            / "runs"
            / f"sats_top30_data_30m_{end.strftime('%Y%m%d')}"
            / "data_cache"
        )
    cache_dir.mkdir(parents=True, exist_ok=True)

    logger.info("fetching Bybit linear tickers ...")
    tickers = _fetch_tickers()
    logger.info("got %d total linear tickers", len(tickers))
    top = _select_top_n(tickers, args.top)
    logger.info("top %d by 24h turnover:", len(top))
    for i, (sym, turnover) in enumerate(top, start=1):
        logger.info("  %2d. %-12s turnover=%.2e USDT", i, sym, turnover)

    symlist_path = cache_dir.parent / "symbols.json"
    symlist_path.parent.mkdir(parents=True, exist_ok=True)
    symlist_path.write_text(
        json.dumps(
            {
                "generated": datetime.now(UTC).isoformat(),
                "fetched_at": datetime.now(UTC).isoformat(),
                "category": "linear",
                "rank_by": "turnover24h",
                "top": args.top,
                "symbols": [s for s, _ in top],
                "turnover24h": {s: t for s, t in top},
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("symbols.json written: %s", symlist_path)

    src = BybitDataSource(cache_dir, category="linear")
    logger.info(
        "prefetching 30m bars %s..%s into cache=%s",
        start.date(), end.date(), cache_dir,
    )
    summary: list[dict[str, object]] = []
    for i, (sym, _turnover) in enumerate(top, start=1):
        logger.info("  [%2d/%d] %s ...", i, len(top), sym)
        try:
            df, gap = src.fetch(symbol=sym, timeframe="30m", start=start, end=end)
            n = df.height
            first_ts = (
                df["timestamp"].min().isoformat()
                if df.height > 0
                else None
            )
            last_ts = (
                df["timestamp"].max().isoformat()
                if df.height > 0
                else None
            )
            summary.append(
                {
                    "symbol": sym,
                    "n_bars": n,
                    "first_ts": first_ts,
                    "last_ts": last_ts,
                    "gap_segments": len(gap.gaps) if gap else 0,
                }
            )
            logger.info(
                "      bars=%d first=%s last=%s gaps=%d",
                n,
                first_ts or "-",
                last_ts or "-",
                len(gap.gaps) if gap else 0,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("      FAILED %s: %s", sym, e)
            summary.append(
                {
                    "symbol": sym,
                    "n_bars": 0,
                    "error": f"{type(e).__name__}: {e}",
                }
            )

    summary_path = cache_dir.parent / "fetch_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    logger.info("fetch summary written: %s", summary_path)

    print()
    print(f"=== Cache built at: {cache_dir} ===")
    full_year = sum(1 for r in summary if int(r.get("n_bars") or 0) >= 17000)
    partial = sum(
        1
        for r in summary
        if 0 < int(r.get("n_bars") or 0) < 17000
    )
    failed = sum(1 for r in summary if r.get("error"))
    print(
        f"  full-year coverage (>=17000 bars): {full_year}\n"
        f"  partial coverage:                  {partial}\n"
        f"  failed:                            {failed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
