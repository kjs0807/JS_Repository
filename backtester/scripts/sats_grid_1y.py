"""SATS strategy grid run — BTC/ETH × {5m, 15m, 30m, 1h, 4h} × 1 year.

Runs 10 backtests with the Phase 4 default ``SATSStrategy`` configuration
(Pine 1/3 split via ``MultiBracketSpec``, ``preset="Auto"``, fixed TP mode,
``allow_short=True``, ``trade_max_age_bars=100``) on Bybit linear perpetual
data fetched on demand via ``BybitDataSource`` (caches under
``runs/sats_grid_1y_<DATE>/data_cache/``).

Output layout::

    backtester/runs/sats_grid_1y_<YYYYMMDD>/
      data_cache/                        # shared parquet cache (auto-fetch)
      BTCUSDT_5m/, BTCUSDT_15m/, ...     # per-run dirs (engine outputs)
      summary.csv                        # 10 rows, see _SUMMARY_COLUMNS
      summary.json                       # same data as machine-readable

Each per-run dir is the standard backtester output (events.jsonl, config.yaml,
results/, exports/) so analysts can drill in via the CLI export / trade-review
tools afterwards on demand. Trade-review HTML is intentionally NOT generated
here — keep the grid lean; render it only for the runs of interest.

Failure isolation: if any run raises, the script logs the traceback and
records ``status="failed"`` + ``error="..."`` in summary.csv for that row,
then continues with the next combination. The grid does not abort.

Usage::

    python -m scripts.sats_grid_1y                 # default 1y window
    python -m scripts.sats_grid_1y --days 180      # shorter window
    python -m scripts.sats_grid_1y --output runs/sats_grid_custom
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
from backtester.events.reader import EventLogReader  # noqa: E402
from backtester.events.types import EventType  # noqa: E402
from backtester.instruments.base import FeeModel, Instrument  # noqa: E402
from backtester.strategies.sats import SATSStrategy  # noqa: E402

UTC = timezone.utc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sats_grid")


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
    """One row of the grid summary."""

    symbol: str
    timeframe: str
    status: str  # "ok" | "failed"
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
    """Generic crypto-perp Instrument tuned for grid backtests.

    Uses Bybit's published ``0.0006`` taker fee (linear perpetual). Tick size
    0.01 is loose vs Bybit's per-symbol mintick (BTC 0.10, ETH 0.01) but
    accurate enough for an aggregate metric grid — fills are floor/ceil-ed
    by the executor, not requantized to exchange-step here.
    """
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


def _build_strategy(timeframe: str) -> SATSStrategy:
    """Phase 4 defaults — ``MultiBracketSpec`` 1/3 split, preset=Auto."""
    return SATSStrategy(
        preset="Auto",
        timeframe_minutes=_TF_MINUTES[timeframe],
        # tp_split_mode="multi" by default — three reduce-only TP legs + SL.
        allow_short=True,
        notional_pct="0.05",
        trade_max_age_bars=100,
    )


def _run_one(
    *,
    symbol: str,
    base: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    grid_root: Path,
    cache_dir: Path,
) -> RunResult:
    """Execute a single (symbol, tf) backtest and collect summary metrics."""
    result = RunResult(
        symbol=symbol,
        timeframe=timeframe,
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
        strategy = _build_strategy(timeframe)
        logger.info("running %s ...", run_id)
        run_result = BacktestEngine(cfg, strategy, verbose=False).run()
        run_dir = Path(run_result.run_dir)
        events_path = Path(run_result.events_path)
        result.run_dir = str(run_dir)
        result.events_path = str(events_path)

        # export_run_data builds the summary.json with metrics we need.
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
        # Verify event log integrity quickly — at least the run produced
        # something readable. ORDER_REJECTED-only runs are still "ok"
        # (the strategy genuinely fired no entries).
        intents_count = sum(
            1 for _ in EventLogReader(events_path).by_type(EventType.INTENT_CREATED)
        )
        if intents_count != result.n_intents:
            logger.warning(
                "%s: summary intent count %d != event log %d",
                run_id, result.n_intents, intents_count,
            )
        result.status = "ok"
        logger.info(
            "  %s OK  fills=%d total_return=%.4f sharpe=%.3f max_dd=%.3f",
            run_id,
            result.n_fills,
            result.total_return or 0.0,
            result.sharpe_ratio or 0.0,
            result.max_drawdown_pct or 0.0,
        )
    except Exception as e:  # noqa: BLE001 — grid must continue past one failure
        tb = traceback.format_exc()
        logger.error("FAILED %s/%s: %s", symbol, timeframe, e)
        # Keep the traceback in error column truncated to one line per cause
        # so the CSV stays readable. Full traceback prints to stderr above.
        result.error = f"{type(e).__name__}: {e}"
        del tb
    return result


def _write_summary(grid_root: Path, results: list[RunResult]) -> None:
    csv_path = grid_root / "summary.csv"
    json_path = grid_root / "summary.json"
    with open(csv_path, "w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(_SUMMARY_COLUMNS))
        writer.writeheader()
        for r in results:
            writer.writerow(r.to_csv_row())
    json_payload = [r.__dict__ for r in results]
    json_path.write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("summary written: %s", csv_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run SATS grid: BTC/ETH x 5m/15m/30m/1h/4h",
    )
    parser.add_argument(
        "--days", type=int, default=365, help="window length in days (default 365)"
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="end date YYYY-MM-DD (default: today UTC)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="grid output dir (default runs/sats_grid_1y_<DATE>)",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="filter symbols (default all of: BTCUSDT ETHUSDT)",
    )
    parser.add_argument(
        "--timeframes",
        nargs="*",
        default=None,
        help="filter timeframes (default all of: 5m 15m 30m 1h 4h)",
    )
    args = parser.parse_args()

    selected_symbols = (
        tuple((s, _BASE_BY_SYMBOL[s]) for s in args.symbols)
        if args.symbols
        else SYMBOLS
    )
    selected_timeframes = (
        tuple(args.timeframes) if args.timeframes else TIMEFRAMES
    )

    if args.end:
        end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    else:
        # Round to UTC midnight so consecutive runs cover the same window.
        now = datetime.now(UTC)
        end = datetime(now.year, now.month, now.day, tzinfo=UTC)
    start = end - timedelta(days=args.days)

    if args.output:
        grid_root = Path(args.output)
    else:
        grid_root = (
            PROJECT_ROOT
            / "runs"
            / f"sats_grid_1y_{end.strftime('%Y%m%d')}"
        )
    cache_dir = grid_root / "data_cache"
    grid_root.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "grid: %d combinations, window %s..%s, output=%s",
        len(selected_symbols) * len(selected_timeframes),
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
                start=start,
                end=end,
                grid_root=grid_root,
                cache_dir=cache_dir,
            )
            results.append(r)
            # Persist after every run so a partial grid still has a usable
            # summary if the script is interrupted.
            _write_summary(grid_root, results)

    print()
    print("=== Grid summary ===")
    print(
        f"{'symbol':<10} {'tf':<5} {'status':<8} {'fills':>6} "
        f"{'return':>10} {'sharpe':>8} {'maxDD':>8}"
    )
    for r in results:
        ret = (
            f"{r.total_return:.4f}" if r.total_return is not None else "-"
        )
        sh = (
            f"{r.sharpe_ratio:.3f}" if r.sharpe_ratio is not None else "-"
        )
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
