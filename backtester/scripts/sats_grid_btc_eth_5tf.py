"""SATS BTC/ETH × {5m, 15m, 30m, 1h, 4h} × {no-gate, score>=30} × 1y.

Reads from the Bybit_Trading SQLite (now back-filled by
``Crypto/Bybit_Trading/scripts/collect_history.py``) so the backtest
sees exactly the same bars the live system would. BBKC-style sizing
(``margin_pct=0.05`` × ``leverage=3``) is used in both runs so the only
varying knob is ``min_signal_score`` — the comparison isolates the
score-gate effect.

Usage::

    python -m scripts.sats_grid_btc_eth_5tf

Outputs two sibling grid directories under ``runs/``:

- ``sats_grid_btc_eth_5tf_nogate_<DATE>/``    — baseline, no score gate
- ``sats_grid_btc_eth_5tf_score30_<DATE>/``  — score >= 30 gate

Each holds standard per-run subdirs (events.jsonl, exports/) plus a
``summary.csv`` aggregating metrics across the 10 (symbol, tf) pairs.
A combined comparison table is printed at the end of the script run.
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
logger = logging.getLogger("sats_grid_btc_eth_5tf")

DEFAULT_DB = (
    Path(__file__).resolve().parent.parent.parent
    / "Crypto"
    / "Bybit_Trading"
    / "db"
    / "bybit_data.db"
)

SYMBOLS: tuple[tuple[str, str], ...] = (
    ("BTCUSDT", "BTC"),
    ("ETHUSDT", "ETH"),
)
TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "30m", "1h", "4h")
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
    min_signal_score: float | None
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


def _build_strategy(timeframe: str, min_score: float | None) -> SATSStrategy:
    return SATSStrategy(
        preset="Auto",
        timeframe_minutes=_TF_MINUTES[timeframe],
        tp_split_mode="multi",
        allow_short=True,
        margin_pct=Decimal("0.05"),
        leverage=Decimal("3"),
        trade_max_age_bars=100,
        min_signal_score=min_score,
    )


def _run_one(
    *,
    symbol: str,
    base: str,
    timeframe: str,
    min_score: float | None,
    start: datetime,
    end: datetime,
    grid_root: Path,
    db_path: Path,
) -> RunResult:
    result = RunResult(
        symbol=symbol,
        timeframe=timeframe,
        min_signal_score=min_score,
        status="failed",
        start=start.isoformat(),
        end=end.isoformat(),
    )
    try:
        run_id = f"{symbol}_{timeframe}"
        cfg = BacktestConfig(
            run_id=run_id,
            data_source=DataSourceConfig(base_dir=db_path, type="sqlite"),
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
            on_run_exists="overwrite",
        )
        strategy = _build_strategy(timeframe, min_score)
        gate = "no-gate" if min_score is None else f"score>={int(min_score)}"
        logger.info("running %s [%s] ...", run_id, gate)
        engine_result = BacktestEngine(cfg, strategy, verbose=False).run()
        run_dir = Path(engine_result.run_dir)
        events_path = Path(engine_result.events_path)
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
            "  %s [%s] OK fills=%d return=%.4f sharpe=%.3f maxDD=%.3f",
            run_id, gate,
            result.n_fills,
            result.total_return or 0.0,
            result.sharpe_ratio or 0.0,
            result.max_drawdown_pct or 0.0,
        )
    except Exception as e:  # noqa: BLE001 — keep the grid going on partial failures
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


def _run_grid(
    *,
    label: str,
    min_score: float | None,
    start: datetime,
    end: datetime,
    db_path: Path,
    output_dir: Path | None,
) -> list[RunResult]:
    if output_dir is not None:
        grid_root = output_dir
    else:
        grid_root = (
            PROJECT_ROOT
            / "runs"
            / f"sats_grid_btc_eth_5tf_{label}_{end.strftime('%Y%m%d')}"
        )
    grid_root.mkdir(parents=True, exist_ok=True)
    logger.info(
        "=== %s grid: %d runs, window %s..%s, output=%s",
        label,
        len(SYMBOLS) * len(TIMEFRAMES),
        start.date(),
        end.date(),
        grid_root,
    )
    results: list[RunResult] = []
    for symbol, base in SYMBOLS:
        for tf in TIMEFRAMES:
            r = _run_one(
                symbol=symbol,
                base=base,
                timeframe=tf,
                min_score=min_score,
                start=start,
                end=end,
                grid_root=grid_root,
                db_path=db_path,
            )
            results.append(r)
            _write_summary(grid_root, results)
    return results


def _print_table(label: str, results: list[RunResult]) -> None:
    print()
    print(f"=== {label} grid summary ===")
    print(
        f"{'symbol':<10} {'tf':<5} {'fills':>6} "
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
            f"{r.symbol:<10} {r.timeframe:<5} {r.n_fills:>6} "
            f"{ret:>10} {sh:>8} {dd:>8}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument(
        "--db-path",
        type=str,
        default=str(DEFAULT_DB),
        help="path to Bybit_Trading SQLite (default: Crypto/Bybit_Trading/db/bybit_data.db)",
    )
    parser.add_argument(
        "--score-thresh",
        type=float,
        default=30.0,
        help="threshold for the gated grid (default 30)",
    )
    parser.add_argument(
        "--output-base",
        type=str,
        default=None,
        help="optional override for grid output directory base name",
    )
    args = parser.parse_args()

    if args.end:
        end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    else:
        now = datetime.now(UTC)
        end = datetime(now.year, now.month, now.day, tzinfo=UTC)
    start = end - timedelta(days=args.days)

    db_path = Path(args.db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    nogate_results = _run_grid(
        label="nogate",
        min_score=None,
        start=start,
        end=end,
        db_path=db_path,
        output_dir=None,
    )
    score_results = _run_grid(
        label=f"score{int(args.score_thresh)}",
        min_score=args.score_thresh,
        start=start,
        end=end,
        db_path=db_path,
        output_dir=None,
    )
    _print_table("no-gate", nogate_results)
    _print_table(f"score>={int(args.score_thresh)}", score_results)

    # Side-by-side comparison.
    by_key = {
        (r.symbol, r.timeframe): r
        for r in nogate_results
    }
    print()
    print(
        f"=== Side-by-side comparison "
        f"(no-gate  vs  score>={int(args.score_thresh)}) ==="
    )
    print(
        f"{'symbol':<10} {'tf':<5} | "
        f"{'fills_n':>7} {'return_n':>10} {'sharpe_n':>9} {'maxDD_n':>8}"
        f"  ||  "
        f"{'fills_g':>7} {'return_g':>10} {'sharpe_g':>9} {'maxDD_g':>8}"
    )
    for r in score_results:
        n = by_key.get((r.symbol, r.timeframe))
        if n is None:
            continue

        def _fmt_ret(x: float | None) -> str:
            return f"{x:.4f}" if x is not None else "-"

        def _fmt_sh(x: float | None) -> str:
            return f"{x:.3f}" if x is not None else "-"

        print(
            f"{r.symbol:<10} {r.timeframe:<5} | "
            f"{n.n_fills:>7} {_fmt_ret(n.total_return):>10} "
            f"{_fmt_sh(n.sharpe_ratio):>9} {_fmt_sh(n.max_drawdown_pct):>8}"
            f"  ||  "
            f"{r.n_fills:>7} {_fmt_ret(r.total_return):>10} "
            f"{_fmt_sh(r.sharpe_ratio):>9} {_fmt_sh(r.max_drawdown_pct):>8}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
