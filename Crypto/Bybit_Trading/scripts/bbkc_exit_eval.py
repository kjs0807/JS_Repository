"""BBKC Exit Round 2 evaluation runner.

Sweeps 12 exit cells × BIGTHREE × 9 walk-forward windows.
Reuses the existing HoldoutSpec/run_strategy_on_holdout pipeline; each
WF window is one HoldoutSpec invocation per (cell, symbol).

Output dir: logs/research/bbkc_squeeze/exit_round/
  - wf_results.jsonl   per-window per-(cell, symbol) metrics
  - auxiliary.json     per-(cell, symbol) auxiliary metrics (avg over windows)
  - summary.json       per-(cell, symbol) WF stability + verdict
  - report.md          human-readable report

Usage:
    python -m scripts.bbkc_exit_eval --smoke         # 1 cell × 1 symbol × 1 window
    python -m scripts.bbkc_exit_eval --full          # all 324 runs
    python -m scripts.bbkc_exit_eval --cell F0 --symbol BTCUSDT
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import load_config
from src.data_manager.db import DBManager
from src.evaluation.holdout import HoldoutSpec, run_strategy_on_holdout
from src.strategies.bbkc_squeeze import BBKCSqueeze
from src.strategies.registry_builder import STRATEGY_CONFIGS


SYMBOLS = ["BTCUSDT", "ETHUSDT", "AVAXUSDT"]
DATA_START = "2024-03-01"
DATA_END = "2026-04-30"
OUTPUT_DIR = PROJECT_ROOT / "logs" / "research" / "bbkc_squeeze" / "exit_round"

logger = logging.getLogger("bbkc_exit_eval")


@dataclass
class WindowResult:
    cell_id: str
    symbol: str
    window_idx: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    oos_pnl: float
    oos_trades: int
    oos_winrate: float
    oos_max_dd: float
    oos_r_per_trade: float


def make_strategy_factory(cell: Dict[str, Any]):
    """Return a zero-arg factory that builds BBKCSqueeze with cell params."""
    kwargs: Dict[str, Any] = dict(
        bb_period=20, bb_std=1.5, kc_period=20, kc_mult=1.0,
        atr_period=14, rsi_period=14, rsi_filter=70.0,
        tp_pct=0.06, sl_pct=0.07, leverage=3, timeframe="1h",
        exit_mode=cell["exit_mode"],
        trail_be_r=1.0,
        trail_start_r=2.0,
        time_stop_bars=cell["time_stop_bars"],
    )
    if cell["trail_distance_r"] is not None:
        kwargs["trail_distance_r"] = cell["trail_distance_r"]
    return lambda: BBKCSqueeze(**kwargs)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BBKC exit-mode WF sweep")
    p.add_argument("--smoke", action="store_true",
                   help="1 cell × 1 symbol × 1 window")
    p.add_argument("--full", action="store_true",
                   help="all 12 cells × 3 symbols × 9 windows = 324 runs")
    p.add_argument("--cell", default=None, help="run only this cell_id (e.g. F0)")
    p.add_argument("--symbol", default=None, help="run only this symbol")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    cells = [c for c in grid if args.cell is None or c["cell_id"] == args.cell]
    symbols = SYMBOLS if args.symbol is None else [args.symbol]
    if args.smoke:
        cells = cells[:1]
        symbols = symbols[:1]

    logger.info("running %d cells × %d symbols", len(cells), len(symbols))
    logger.info("cells: %s", [c["cell_id"] for c in cells])
    logger.info("symbols: %s", symbols)
    # Window logic + execution loop added in subsequent tasks.


if __name__ == "__main__":
    main()
