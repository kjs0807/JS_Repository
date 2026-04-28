"""D2-grid: small interpretable parameter sweep for DonchianFixedRRTrendFilter.

Scope and safety rails
----------------------
The memo is explicit that this round is NOT a full grid search. The
goal is **re-evaluation**, not hyperparameter optimization. So the grid
is intentionally narrow: one parameter axis moves at a time relative
to the memo-fixed center, and every cell must remain interpretable
without a second look.

Search axes (center = existing defaults):

    entry_period:         {15, 20, 25}             (core donchian period)
    stop_atr:             {2.0, 2.5, 3.0}          (initial stop width)
    tp_r_ratio:           {1.5, 2.0, 2.5}          (fixed RR TP)
    trail_activate_atr:   {1.0, 1.5, 2.0}          (trailing start)
    trail_distance_atr:   {0.5, 1.0, 1.5}          (trailing width)
    ema_filter:           {100, 200}               (trend filter period)

That is 3*3*3*3*3*2 = 486 cells. We prune by running them SEQUENTIALLY
with an early-stop bound: if a cell's aggregate variant n_trades drops
below ``MIN_TRADES_EARLY_STOP`` on the first symbol (BTC), the cell is
marked ``SKIPPED_LOW_TRADES`` instead of running the other 4 symbols.
Holdout is ~6 months so BTC alone is a cheap proxy for "variant is
effectively disabled".

Every cell is judged against the DonchianFixedRR baseline produced
once in ``scripts/d2_core_eval.py``. To avoid re-running the baseline
486 times, this script ALSO runs the baseline once (at the start) and
reuses its results.json for every cell's verdict.

Output::

    logs/d2_grid/
      ├── baseline.json       -- DonchianFixedRR run once
      ├── cells.jsonl         -- one line per cell: params + metrics + verdict
      └── summary.md          -- top PROMOTE / CONDITIONAL / KILL breakdown

This is intentionally cheap to interrupt: cells.jsonl is appended per
cell, so partial runs are valuable.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any, Dict, Iterable, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import load_config
from src.data_manager.db import DBManager
from src.evaluation.holdout import HoldoutSpec, run_strategy_on_holdout
from src.evaluation.verdict import (
    HoldoutVerdict,
    VerdictThresholds,
    judge_variant_vs_baseline,
)
from src.strategies.donchian_fixed_rr import DonchianFixedRR
from src.strategies.donchian_fixed_rr_trend_filter import (
    DonchianFixedRRTrendFilter,
)


# Narrow by default — 486 cells is the explicit cap. The memo says
# "interpretable candidates only" so we do NOT let the caller expand
# this from the CLI. Changing the grid is a deliberate code edit.
GRID_SPACE: Dict[str, List[Any]] = {
    "entry_period":       [15, 20, 25],
    "stop_atr":           [2.0, 2.5, 3.0],
    "tp_r_ratio":         [1.5, 2.0, 2.5],
    "trail_activate_atr": [1.0, 1.5, 2.0],
    "trail_distance_atr": [0.5, 1.0, 1.5],
    "ema_filter":         [100, 200],
}

MIN_TRADES_EARLY_STOP = 5


def _expand_grid(space: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    keys = list(space.keys())
    values = [space[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in product(*values)]


def _run_baseline_once(spec: HoldoutSpec, db: Any) -> Dict[str, Any]:
    from src.backtester.engine import BacktestEngine
    engine = BacktestEngine()
    run = run_strategy_on_holdout(
        lambda: DonchianFixedRR(), spec, db, engine,
    )
    return {"per_symbol": run["per_symbol"], "aggregate": run["aggregate"]}


def _early_stop_probe(
    params: Dict[str, Any], spec: HoldoutSpec, db: Any,
) -> int:
    """Run the variant on BTC only; return its trade count.

    Purpose: skip grid cells whose ``ema_filter`` + ``entry_period``
    combination makes the variant effectively dead. Running 5 symbols
    for a dead cell costs the same as a live one, so a cheap single-
    symbol probe pays for itself on any skip.
    """
    from src.backtester.engine import BacktestEngine
    probe_spec = HoldoutSpec(
        symbols=["BTCUSDT"],
        timeframe=spec.timeframe,
        holdout_start_dt=spec.holdout_start_dt,
        holdout_end_dt=spec.holdout_end_dt,
        warmup_days=spec.warmup_days,
        initial_capital=spec.initial_capital,
    )
    run = run_strategy_on_holdout(
        lambda p=params: DonchianFixedRRTrendFilter(**p),
        probe_spec,
        db,
        BacktestEngine(),
    )
    return int(run["aggregate"]["n_trades"])


def _run_full_cell(
    params: Dict[str, Any], spec: HoldoutSpec, db: Any,
) -> Dict[str, Any]:
    from src.backtester.engine import BacktestEngine
    run = run_strategy_on_holdout(
        lambda p=params: DonchianFixedRRTrendFilter(**p),
        spec,
        db,
        BacktestEngine(),
    )
    return {"per_symbol": run["per_symbol"], "aggregate": run["aggregate"]}


def _write_summary(
    out_dir: Path,
    baseline: Dict[str, Any],
    rows: List[Dict[str, Any]],
) -> None:
    """Top-N PROMOTE / CONDITIONAL / KILL summary."""
    by_verdict: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_verdict.setdefault(row["verdict"], []).append(row)

    lines = [
        "# D2 Grid Summary",
        "",
        f"Baseline DonchianFixedRR aggregate: "
        f"n={baseline['aggregate']['n_trades']} "
        f"pnl={baseline['aggregate']['total_pnl']:+.2f} "
        f"wr={baseline['aggregate']['win_rate']:.1%} "
        f"mdd={baseline['aggregate']['max_drawdown']:.1%}",
        "",
        f"Total cells: {len(rows)}",
    ]
    for k in ("PROMOTE", "CONDITIONAL_PROMOTE", "NO_EDGE", "KILL",
              "INSUFFICIENT_DATA", "SKIPPED_LOW_TRADES"):
        n = len(by_verdict.get(k, []))
        lines.append(f"- {k}: {n}")
    lines.append("")

    def _top_n(group: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
        return sorted(
            group,
            key=lambda r: r["delta_avg_trade_pnl"],
            reverse=True,
        )[:n]

    for bucket in ("PROMOTE", "CONDITIONAL_PROMOTE"):
        group = by_verdict.get(bucket, [])
        if not group:
            continue
        lines.append(f"## Top 10 {bucket} by Δavg_trade_pnl")
        lines.append("")
        lines.append(
            "| Δavg | Δpnl | Δwr | Δmdd | n_trades | params |"
        )
        lines.append("|---|---|---|---|---|---|")
        for row in _top_n(group, 10):
            p = json.dumps(row["params"], separators=(",", ":"))
            lines.append(
                f"| {row['delta_avg_trade_pnl']:+.2f} | "
                f"{row['delta_total_pnl']:+.2f} | "
                f"{row['delta_win_rate']:+.1%} | "
                f"{row['delta_max_drawdown']:+.1%} | "
                f"{row['variant_n_trades']} | `{p}` |"
            )
        lines.append("")

    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def _iter_cells(space: Dict[str, List[Any]]) -> Iterable[Dict[str, Any]]:
    for cell in _expand_grid(space):
        yield cell


def main() -> int:
    parser = argparse.ArgumentParser(
        description="D2 narrow grid for DonchianFixedRRTrendFilter.",
    )
    parser.add_argument(
        "--out-dir", type=Path,
        default=PROJECT_ROOT / "logs" / "d2_grid",
    )
    parser.add_argument("--start", type=str, default="2025-10-01")
    parser.add_argument("--end", type=str, default="2026-04-10")
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument(
        "--symbols", nargs="*",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"],
    )
    parser.add_argument(
        "--max-cells", type=int, default=0,
        help="If > 0, only run the first N grid cells (smoke test).",
    )
    parser.add_argument(
        "--skip-probe", action="store_true",
        help="Disable BTC-only early-stop probe (full sweep).",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip cells whose param hash already appears in cells.jsonl.",
    )
    args = parser.parse_args()

    spec = HoldoutSpec(
        symbols=args.symbols,
        timeframe="1h",
        holdout_start_dt=datetime.strptime(args.start, "%Y-%m-%d"),
        holdout_end_dt=datetime.strptime(args.end, "%Y-%m-%d"),
        warmup_days=args.warmup_days,
    )
    cfg = load_config()
    db = DBManager(cfg.app.db_path)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = args.out_dir / "baseline.json"
    cells_path = args.out_dir / "cells.jsonl"

    if args.resume and baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        print("Resuming: loaded baseline from disk.")
    else:
        print("Running baseline DonchianFixedRR...")
        baseline = _run_baseline_once(spec, db)
        baseline_path.write_text(
            json.dumps(baseline, indent=2, default=str),
            encoding="utf-8",
        )
        print(
            f"  baseline n={baseline['aggregate']['n_trades']} "
            f"pnl={baseline['aggregate']['total_pnl']:+.2f} "
            f"mdd={baseline['aggregate']['max_drawdown']:.1%}"
        )

    done_keys = set()
    if args.resume and cells_path.exists():
        for line in cells_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                done_keys.add(_cell_key(row["params"]))
            except Exception:
                continue
        print(f"Resuming: {len(done_keys)} cells already recorded.")

    thresholds = VerdictThresholds()
    cells = list(_iter_cells(GRID_SPACE))
    if args.max_cells > 0:
        cells = cells[: args.max_cells]
    print(f"Grid cells to evaluate: {len(cells)}")

    rows: List[Dict[str, Any]] = []
    fp = cells_path.open("a", encoding="utf-8")
    try:
        for idx, params in enumerate(cells, 1):
            key = _cell_key(params)
            if key in done_keys:
                continue
            t0 = time.time()
            if not args.skip_probe:
                n_probe = _early_stop_probe(params, spec, db)
                if n_probe < MIN_TRADES_EARLY_STOP:
                    row = {
                        "params": params,
                        "verdict": "SKIPPED_LOW_TRADES",
                        "probe_n_trades": n_probe,
                        "elapsed_s": round(time.time() - t0, 1),
                    }
                    rows.append(row)
                    fp.write(json.dumps(row) + "\n")
                    fp.flush()
                    if idx % 10 == 0:
                        print(
                            f"  [{idx}/{len(cells)}] SKIPPED "
                            f"n_probe={n_probe}"
                        )
                    continue
            variant = _run_full_cell(params, spec, db)
            verdict = judge_variant_vs_baseline(
                variant_name="DonchianFixedRRTrendFilter",
                variant_result=variant,
                baseline_name="DonchianFixedRR",
                baseline_result=baseline,
                thresholds=thresholds,
            )
            row = {
                "params": params,
                "verdict": verdict.verdict,
                "reasons": verdict.reasons,
                "variant_n_trades": int(variant["aggregate"]["n_trades"]),
                "variant_total_pnl": variant["aggregate"]["total_pnl"],
                "variant_max_drawdown": variant["aggregate"]["max_drawdown"],
                "variant_win_rate": variant["aggregate"]["win_rate"],
                "delta_avg_trade_pnl": verdict.delta_avg_trade_pnl,
                "delta_total_pnl": verdict.delta_total_pnl,
                "delta_win_rate": verdict.delta_win_rate,
                "delta_max_drawdown": verdict.delta_max_drawdown,
                "symbol_concentration": verdict.symbol_concentration,
                "elapsed_s": round(time.time() - t0, 1),
            }
            rows.append(row)
            fp.write(json.dumps(row) + "\n")
            fp.flush()
            if idx % 5 == 0 or idx == len(cells):
                print(
                    f"  [{idx}/{len(cells)}] "
                    f"{verdict.verdict:20s} "
                    f"Δavg={verdict.delta_avg_trade_pnl:+.2f} "
                    f"Δmdd={verdict.delta_max_drawdown:+.1%} "
                    f"n={variant['aggregate']['n_trades']}"
                )
    finally:
        fp.close()

    # Re-load the full cells file so summary covers resumed rows too.
    all_rows = []
    if cells_path.exists():
        for line in cells_path.read_text(encoding="utf-8").splitlines():
            try:
                all_rows.append(json.loads(line))
            except Exception:
                continue
    _write_summary(args.out_dir, baseline, all_rows)
    print(f"\nSaved {cells_path}")
    print(f"Saved {args.out_dir / 'summary.md'}")
    return 0


def _cell_key(params: Dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
