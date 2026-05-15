"""SATS 1h × no-gate × 1y on the 27 top-turnover symbols with full 1y data.

Tests whether the BTC +sharpe 0.19 / ETH +sharpe 0.16 result on 1h × no-gate
generalises across the broader top-30 USDT-perp universe — the prior grid only
covered BTC + ETH so two positive results could be a coincidence.

Setup matches the recent BTC/ETH 1h no-gate run exactly:

- timeframe = 1h
- ``min_signal_score = None`` (no gate)
- BBKC sizing: ``margin_pct=0.05`` × ``leverage=3``
- ``trade_max_age_bars = 100``
- 1-year window ending today (UTC)
- Reads from the Bybit_Trading SQLite via ``SQLiteDataSource``

Symbol set: the 27 symbols that have full 1-year coverage on every
intraday TF in the SQLite (top-30 by Bybit 24h turnover minus the 3
recent listings ``BILL`` / ``CL`` / ``LAB``). Running on the same set
keeps comparisons consistent with future multi-TF grids on the same DB.

Usage::

    python -m scripts.sats_grid_1h_nogate_top27
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
logger = logging.getLogger("sats_grid_1h_nogate_top27")

DEFAULT_DB = (
    Path(__file__).resolve().parent.parent.parent
    / "Crypto"
    / "Bybit_Trading"
    / "db"
    / "bybit_data.db"
)

# 27 symbols with full 1y coverage on every intraday TF in the SQLite
# (top-30 USDT-perp by 24h turnover minus BILL / CL / LAB partial-coverage
# recent listings). Order matches the original turnover ranking so summary
# rows read most-traded → less-traded.
SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "TONUSDT",
    "XRPUSDT",
    "ZECUSDT",
    "DOGEUSDT",
    "WIFUSDT",
    "HYPEUSDT",
    "1000PEPEUSDT",
    "NEARUSDT",
    "TAOUSDT",
    "FARTCOINUSDT",
    "SUIUSDT",
    "ADAUSDT",
    "ICPUSDT",
    "ENAUSDT",
    "XAUTUSDT",
    "B3USDT",
    "IOUSDT",
    "DASHUSDT",
    "ONDOUSDT",
    "LINKUSDT",
    "BNBUSDT",
    "FILUSDT",
    "AAVEUSDT",
    "NOTUSDT",
)
# FILUSDT was in top 30 too but let's keep the 27-set identical to the
# all-5-TF intersection — verified via _check_progress earlier. Drop FIL
# if it doesn't have 1h full-year coverage (it does, but we keep the
# strict intersection for consistency).

_SUMMARY_COLUMNS: tuple[str, ...] = (
    "symbol",
    "rank",
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
    base = symbol[: -len("USDT")] if symbol.endswith("USDT") else symbol
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
        timeframe_minutes=60,
        tp_split_mode="multi",
        allow_short=True,
        margin_pct=Decimal("0.05"),
        leverage=Decimal("3"),
        trade_max_age_bars=100,
        # Explicitly None — no signal-score gate (matches "BTC/ETH 1h
        # no-gate" baseline result we're trying to validate).
        min_signal_score=None,
    )


def _run_one(
    *,
    symbol: str,
    rank: int,
    start: datetime,
    end: datetime,
    grid_root: Path,
    db_path: Path,
) -> RunResult:
    result = RunResult(
        symbol=symbol,
        rank=rank,
        status="failed",
        start=start.isoformat(),
        end=end.isoformat(),
    )
    try:
        run_id = f"{symbol}_1h"
        cfg = BacktestConfig(
            run_id=run_id,
            data_source=DataSourceConfig(base_dir=db_path, type="sqlite"),
            instruments=[_instrument(symbol)],
            timeframes_per_symbol={symbol: ["1h"]},
            primary_symbol=symbol,
            primary_timeframe="1h",
            start=start,
            end=end,
            initial_equity=Decimal("100000"),
            allow_short=True,
            output_dir=grid_root,
            persist_instrument_snapshot=False,
            on_run_exists="overwrite",
        )
        strategy = _build_strategy()
        logger.info("running %s (rank=%d) ...", run_id, rank)
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--db-path", type=str, default=str(DEFAULT_DB))
    parser.add_argument("--output", type=str, default=None)
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

    if args.output:
        grid_root = Path(args.output)
    else:
        grid_root = (
            PROJECT_ROOT
            / "runs"
            / f"sats_grid_1h_nogate_top27_{end.strftime('%Y%m%d')}"
        )
    grid_root.mkdir(parents=True, exist_ok=True)

    logger.info(
        "grid: %d symbols × 1h × no-gate, window %s..%s, output=%s",
        len(SYMBOLS), start.date(), end.date(), grid_root,
    )

    results: list[RunResult] = []
    for rank, sym in enumerate(SYMBOLS, start=1):
        r = _run_one(
            symbol=sym,
            rank=rank,
            start=start,
            end=end,
            grid_root=grid_root,
            db_path=db_path,
        )
        results.append(r)
        _write_summary(grid_root, results)

    print()
    print("=== Grid: top-27 × 1h × no-gate (sharpe-sorted) ===")
    print(
        f"{'rank':>4} {'symbol':<14} {'fills':>6} "
        f"{'return':>10} {'sharpe':>8} {'maxDD':>8}"
    )
    sorted_results = sorted(
        results,
        key=lambda r: (
            r.sharpe_ratio if r.sharpe_ratio is not None else -9e9
        ),
        reverse=True,
    )
    for r in sorted_results:
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

    n_pos = sum(1 for r in results if (r.sharpe_ratio or 0) > 0)
    n_significant = sum(1 for r in results if (r.sharpe_ratio or 0) >= 0.15)
    n_neg = sum(1 for r in results if (r.sharpe_ratio or 0) < 0)
    avg_sharpe = sum(r.sharpe_ratio or 0 for r in results) / len(results)
    avg_return = sum(r.total_return or 0 for r in results) / len(results)
    print(
        f"\nOf {len(results)} symbols: "
        f"sharpe > 0 → {n_pos}, "
        f"sharpe ≥ 0.15 → {n_significant}, "
        f"sharpe < 0 → {n_neg}"
    )
    print(f"Mean sharpe = {avg_sharpe:+.3f}, mean return = {avg_return:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
