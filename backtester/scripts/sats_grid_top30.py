"""SATS grid — top-30 USDT-perp × 30m × 1y × score≥30 + BBKC sizing.

Reads the symbol list and per-symbol coverage from
``runs/sats_top30_data_30m_<DATE>/`` (produced by
:mod:`scripts.fetch_top30_30m`), drops any symbol whose cached parquet has
< ``--min-bars`` rows (default 17000 = effectively a full 1-year 30m
window — partial-coverage recent listings can't carry a meaningful
backtest), and runs the Phase 4 SATS strategy with:

- ``min_signal_score = 30`` (score gate)
- ``margin_pct = 0.05`` × ``leverage = 3`` (BBKC pattern → ~15% notional
  per trade via ``TargetMarginPct``)
- ``preset = "Auto"`` → 30m falls into the "Default" preset table
- ``allow_short = True``, ``trade_max_age_bars = 100``
- ``move_sl_to_entry_on_tp1 = False`` (kept off for clean comparison)

Output dir: ``runs/sats_grid_top30_score30_30m_<DATE>/``. Summary CSV +
JSON contain one row per symbol with metric + run-dir pointers.

Usage::

    python -m scripts.sats_grid_top30
    python -m scripts.sats_grid_top30 --min-bars 8000   # accept 6-month-only
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from backtester.analysis.export import export_run_data  # noqa: E402
from backtester.core.config import BacktestConfig, DataSourceConfig  # noqa: E402
from backtester.core.engine import BacktestEngine  # noqa: E402
from backtester.instruments.base import FeeModel, Instrument  # noqa: E402
from backtester.strategies.sats import SATSStrategy  # noqa: E402

UTC = timezone.utc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sats_grid_top30")

_SUMMARY_COLUMNS: tuple[str, ...] = (
    "symbol",
    "rank",
    "n_cache_bars",
    "status",
    "start",
    "end",
    "n_intents",
    "n_fills",
    "final_equity",
    "total_return",
    "max_drawdown_pct",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "n_periods",
    "run_dir",
    "events_path",
    "error",
)


@dataclass
class RunResult:
    symbol: str
    rank: int
    n_cache_bars: int
    status: str
    start: str
    end: str
    n_intents: int = 0
    n_fills: int = 0
    final_equity: float | None = None
    total_return: float | None = None
    max_drawdown_pct: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    n_periods: int | None = None
    run_dir: str = ""
    events_path: str = ""
    error: str = ""

    def to_csv_row(self) -> dict[str, str]:
        def _str(v: object) -> str:
            if v is None:
                return ""
            if isinstance(v, float):
                return f"{v:.6f}"
            return str(v)

        return {col: _str(getattr(self, col)) for col in _SUMMARY_COLUMNS}


def _instrument(symbol: str) -> Instrument:
    """Generic crypto-perp Instrument — same template as ``sats_grid_1y``."""
    base = symbol.removesuffix("USDT")
    if symbol.startswith("1000"):
        # ``1000PEPEUSDT`` etc. — base is everything before USDT.
        base = symbol[: -len("USDT")]
    return Instrument(
        symbol=symbol,
        asset_class="crypto_perp",
        tick_size=Decimal("0.0001"),
        tick_value=Decimal("0.0001"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency=base,
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )


def _build_strategy() -> SATSStrategy:
    return SATSStrategy(
        preset="Auto",
        timeframe_minutes=30,
        tp_split_mode="multi",
        allow_short=True,
        # BBKC-pattern sizing — equity × 5% × 3x leverage = 15% notional.
        margin_pct=Decimal("0.05"),
        leverage=Decimal("3"),
        trade_max_age_bars=100,
        min_signal_score=30,
    )


def _run_one(
    *,
    symbol: str,
    rank: int,
    n_cache_bars: int,
    start: datetime,
    end: datetime,
    grid_root: Path,
    cache_dir: Path,
) -> RunResult:
    result = RunResult(
        symbol=symbol,
        rank=rank,
        n_cache_bars=n_cache_bars,
        status="failed",
        start=start.isoformat(),
        end=end.isoformat(),
    )
    try:
        run_id = f"{symbol}_30m"
        cfg = BacktestConfig(
            run_id=run_id,
            data_source=DataSourceConfig(base_dir=cache_dir, type="bybit"),
            instruments=[_instrument(symbol)],
            timeframes_per_symbol={symbol: ["30m"]},
            primary_symbol=symbol,
            primary_timeframe="30m",
            start=start,
            end=end,
            initial_equity=Decimal("100000"),
            allow_short=True,
            output_dir=grid_root,
            persist_instrument_snapshot=False,
            on_run_exists="overwrite",
        )
        strategy = _build_strategy()
        logger.info("running %s (rank=%d, cache_bars=%d) ...", run_id, rank, n_cache_bars)
        run_result = BacktestEngine(cfg, strategy, verbose=False).run()
        run_dir = Path(run_result.run_dir)
        events_path = Path(run_result.events_path)
        result.run_dir = str(run_dir)
        result.events_path = str(events_path)

        exports = export_run_data(run_dir)
        with open(exports["summary"], encoding="utf-8") as fp:
            summary = json.load(fp)
        result.n_intents = int(summary.get("n_intents") or 0)
        result.n_fills = int(summary.get("n_fills") or 0)
        result.final_equity = summary.get("final_equity")
        result.total_return = summary.get("total_return")
        result.max_drawdown_pct = summary.get("max_drawdown_pct")
        result.sharpe_ratio = summary.get("sharpe_ratio")
        result.sortino_ratio = summary.get("sortino_ratio")
        result.calmar_ratio = summary.get("calmar_ratio")
        result.n_periods = summary.get("n_periods")
        result.status = "ok"
        logger.info(
            "  %s OK fills=%d return=%.4f sharpe=%.3f maxDD=%.3f",
            run_id,
            result.n_fills,
            result.total_return or 0.0,
            result.sharpe_ratio or 0.0,
            result.max_drawdown_pct or 0.0,
        )
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        logger.error("FAILED %s: %s", symbol, e)
        result.error = f"{type(e).__name__}: {e}"
    return result


def _write_summary(grid_root: Path, results: list[RunResult]) -> None:
    csv_path = grid_root / "summary.csv"
    json_path = grid_root / "summary.json"
    with open(csv_path, "w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(_SUMMARY_COLUMNS))
        writer.writeheader()
        for r in results:
            writer.writerow(r.to_csv_row())
    json_path.write_text(
        json.dumps([r.__dict__ for r in results], indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("summary written: %s", csv_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="path to fetch_top30_30m output (default: latest by date suffix)",
    )
    parser.add_argument(
        "--min-bars",
        type=int,
        default=17000,
        help="exclude symbols with fewer cached bars (default 17000 = full 1y)",
    )
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    if args.end:
        end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    else:
        now = datetime.now(UTC)
        end = datetime(now.year, now.month, now.day, tzinfo=UTC)
    start = end - timedelta(days=args.days)

    if args.data_dir:
        data_root = Path(args.data_dir)
    else:
        # Pick the freshest sats_top30_data_30m_* dir.
        candidates = sorted(
            (PROJECT_ROOT / "runs").glob("sats_top30_data_30m_*"),
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError(
                "no sats_top30_data_30m_* dir found — run "
                "scripts.fetch_top30_30m first"
            )
        data_root = candidates[0]
    cache_dir = data_root / "data_cache"
    if not cache_dir.exists():
        raise FileNotFoundError(f"cache dir missing: {cache_dir}")
    fetch_summary_path = data_root / "fetch_summary.json"
    if not fetch_summary_path.exists():
        raise FileNotFoundError(
            f"fetch_summary.json missing: {fetch_summary_path}"
        )
    fetch_summary = json.loads(fetch_summary_path.read_text(encoding="utf-8"))
    # Preserve ranking order from the original fetch (already sorted by
    # 24h turnover descending).
    eligible: list[tuple[str, int, int]] = []
    skipped: list[tuple[str, int, str]] = []
    for rank, row in enumerate(fetch_summary, start=1):
        sym = str(row.get("symbol") or "")
        n_bars = int(row.get("n_bars") or 0)
        err = row.get("error")
        if err:
            skipped.append((sym, n_bars, f"fetch error: {err}"))
            continue
        if n_bars < args.min_bars:
            skipped.append(
                (sym, n_bars, f"only {n_bars} bars < {args.min_bars}")
            )
            continue
        eligible.append((sym, rank, n_bars))

    if args.output:
        grid_root = Path(args.output)
    else:
        grid_root = (
            PROJECT_ROOT
            / "runs"
            / f"sats_grid_top30_score30_30m_{end.strftime('%Y%m%d')}"
        )
    grid_root.mkdir(parents=True, exist_ok=True)

    logger.info(
        "grid: %d eligible / %d skipped, window %s..%s, output=%s",
        len(eligible), len(skipped),
        start.date(), end.date(), grid_root,
    )
    for sym, n, why in skipped:
        logger.info("  skip %-15s (%d bars) — %s", sym, n, why)

    results: list[RunResult] = []
    for symbol, rank, n_cache_bars in eligible:
        r = _run_one(
            symbol=symbol,
            rank=rank,
            n_cache_bars=n_cache_bars,
            start=start,
            end=end,
            grid_root=grid_root,
            cache_dir=cache_dir,
        )
        results.append(r)
        _write_summary(grid_root, results)

    print()
    print("=== Grid summary (top-30 × 30m × score≥30 × 3x leverage) ===")
    print(
        f"{'rank':>4} {'symbol':<14} {'fills':>6} "
        f"{'return':>10} {'sharpe':>8} {'maxDD':>8}"
    )
    # Sort display by sharpe desc to surface the best.
    sorted_for_display = sorted(
        results,
        key=lambda r: (
            r.sharpe_ratio if r.sharpe_ratio is not None else -9e9
        ),
        reverse=True,
    )
    for r in sorted_for_display:
        ret = f"{r.total_return:.4f}" if r.total_return is not None else "-"
        sh = f"{r.sharpe_ratio:.3f}" if r.sharpe_ratio is not None else "-"
        dd = (
            f"{r.max_drawdown_pct:.3f}"
            if r.max_drawdown_pct is not None
            else "-"
        )
        print(
            f"{r.rank:>4} {r.symbol:<14} {r.n_fills:>6} "
            f"{ret:>10} {sh:>8} {dd:>8}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
