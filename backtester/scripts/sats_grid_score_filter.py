"""SATS grid with Pine signal-score filter (``min_signal_score``).

Drives the same BTC/ETH × {5m,15m,30m,1h,4h} × 1y grid as
:mod:`scripts.sats_grid_1y` but adds a score threshold so only signals
whose Pine ``calcSignalScore`` (sum of 6 sub-scores, max ~100) clears
the bar are taken. Otherwise identical defaults: multi 1/3 split, no
BE move, ``allow_short=True``, ``notional_pct=0.05``,
``trade_max_age_bars=100``.

Usage::

    python -m scripts.sats_grid_score_filter --min-score 20
    python -m scripts.sats_grid_score_filter --min-score 30
    python -m scripts.sats_grid_score_filter --min-score 0   # no filter (sanity)

Output dir: ``runs/sats_grid_score{N}_<DATE>/``.
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
logger = logging.getLogger("sats_grid_score")

SYMBOLS: tuple[tuple[str, str], ...] = (
    ("BTCUSDT", "BTC"),
    ("ETHUSDT", "ETH"),
)
TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "30m", "1h", "4h")
_BASE_BY_SYMBOL: dict[str, str] = {sym: base for sym, base in SYMBOLS}
_TF_MINUTES: dict[str, int] = {
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
}

_SUMMARY_COLUMNS: tuple[str, ...] = (
    "symbol",
    "timeframe",
    "min_signal_score",
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
    timeframe: str
    min_signal_score: float
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


def _instrument(symbol: str, base: str) -> Instrument:
    return Instrument(
        symbol=symbol,
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency=base,
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )


def _build_strategy(timeframe: str, min_signal_score: float) -> SATSStrategy:
    return SATSStrategy(
        preset="Auto",
        timeframe_minutes=_TF_MINUTES[timeframe],
        tp_split_mode="multi",
        allow_short=True,
        notional_pct="0.05",
        trade_max_age_bars=100,
        min_signal_score=min_signal_score if min_signal_score > 0 else None,
    )


def _run_one(
    *,
    symbol: str,
    base: str,
    timeframe: str,
    min_signal_score: float,
    start: datetime,
    end: datetime,
    grid_root: Path,
    cache_dir: Path,
) -> RunResult:
    result = RunResult(
        symbol=symbol,
        timeframe=timeframe,
        min_signal_score=min_signal_score,
        status="failed",
        start=start.isoformat(),
        end=end.isoformat(),
    )
    try:
        run_id = f"{symbol}_{timeframe}"
        cfg = BacktestConfig(
            run_id=run_id,
            data_source=DataSourceConfig(base_dir=cache_dir, type="bybit"),
            instruments=[_instrument(symbol, base)],
            timeframes_per_symbol={symbol: [timeframe]},
            primary_symbol=symbol,
            primary_timeframe=timeframe,
            start=start,
            end=end,
            initial_equity=Decimal("100000"),
            allow_short=True,
            output_dir=grid_root,
            persist_instrument_snapshot=False,
        )
        strategy = _build_strategy(timeframe, min_signal_score)
        logger.info("running %s (min_score=%.1f) ...", run_id, min_signal_score)
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
            "  %s OK fills=%d total_return=%.4f sharpe=%.3f max_dd=%.3f",
            run_id,
            result.n_fills,
            result.total_return or 0.0,
            result.sharpe_ratio or 0.0,
            result.max_drawdown_pct or 0.0,
        )
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        logger.error("FAILED %s/%s: %s", symbol, timeframe, e)
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
        "--min-score",
        type=float,
        required=True,
        help="signal score threshold (Pine 0..~100). 0 disables the filter.",
    )
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--timeframes", nargs="*", default=None)
    args = parser.parse_args()

    if args.end:
        end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    else:
        now = datetime.now(UTC)
        end = datetime(now.year, now.month, now.day, tzinfo=UTC)
    start = end - timedelta(days=args.days)

    if args.output:
        grid_root = Path(args.output)
    else:
        grid_root = (
            PROJECT_ROOT
            / "runs"
            / f"sats_grid_score{int(args.min_score)}_{end.strftime('%Y%m%d')}"
        )
    cache_dir = grid_root / "data_cache"
    grid_root.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    selected_symbols = (
        tuple((s, _BASE_BY_SYMBOL[s]) for s in args.symbols)
        if args.symbols
        else SYMBOLS
    )
    selected_timeframes = (
        tuple(args.timeframes) if args.timeframes else TIMEFRAMES
    )
    logger.info(
        "grid: %d combinations, min_score=%.1f, window %s..%s, output=%s",
        len(selected_symbols) * len(selected_timeframes),
        args.min_score,
        start.date(),
        end.date(),
        grid_root,
    )

    results: list[RunResult] = []
    for symbol, base in selected_symbols:
        for tf in selected_timeframes:
            r = _run_one(
                symbol=symbol,
                base=base,
                timeframe=tf,
                min_signal_score=args.min_score,
                start=start,
                end=end,
                grid_root=grid_root,
                cache_dir=cache_dir,
            )
            results.append(r)
            _write_summary(grid_root, results)

    print()
    print(f"=== Grid summary (min_score={args.min_score}) ===")
    print(
        f"{'symbol':<10} {'tf':<5} {'status':<8} {'fills':>6} "
        f"{'return':>10} {'sharpe':>8} {'maxDD':>8}"
    )
    for r in results:
        ret = f"{r.total_return:.4f}" if r.total_return is not None else "-"
        sh = f"{r.sharpe_ratio:.3f}" if r.sharpe_ratio is not None else "-"
        dd = (
            f"{r.max_drawdown_pct:.3f}"
            if r.max_drawdown_pct is not None
            else "-"
        )
        print(
            f"{r.symbol:<10} {r.timeframe:<5} {r.status:<8} {r.n_fills:>6} "
            f"{ret:>10} {sh:>8} {dd:>8}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
