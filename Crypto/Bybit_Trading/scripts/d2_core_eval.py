"""D2-core re-evaluation: DonchianFixedRRTrendFilter vs DonchianFixedRR.

Round 1 already ran this comparison on the shared 5-symbol holdout
(2025-10-01 to 2026-04-10). Round 1 found:

    DonchianFixedRR            total -$2805  50.4% WR  MDD 39.3%
    DonchianFixedRRTrendFilter total  -$296  56.8% WR  MDD 19.6%

The variant is cleaner on every axis but still net negative. Before
promoting it as a new baseline candidate we want a self-contained
re-run that:

1. Uses the same ``HoldoutSpec`` the rest of the rule-based workflow
   uses (``src/evaluation/holdout.py``).
2. Emits the exact verdict JSON consumed by ``scripts/holdout_verdict.py``.
3. Saves per-symbol breakdown + aggregate + the automated verdict in
   one directory so downstream docs can cite a single path.

This script is intentionally small — all the real work lives in
``src/evaluation``. The value is the **wired pipeline**: one command
now reproduces the D2 baseline comparison end-to-end.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import load_config
from src.data_manager.db import DBManager
from src.evaluation.holdout import HoldoutSpec, run_strategies_on_holdout
from src.evaluation.verdict import (
    format_verdict_line,
    judge_variant_vs_baseline,
)
from src.strategies.donchian_fixed_rr import DonchianFixedRR
from src.strategies.donchian_fixed_rr_trend_filter import (
    DonchianFixedRRTrendFilter,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="D2-core: DonchianFixedRRTrendFilter vs DonchianFixedRR.",
    )
    parser.add_argument(
        "--out-dir", type=Path,
        default=PROJECT_ROOT / "logs" / "d2_core",
    )
    parser.add_argument("--start", type=str, default="2025-10-01")
    parser.add_argument("--end", type=str, default="2026-04-10")
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument(
        "--symbols", nargs="*",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"],
    )
    args = parser.parse_args()

    spec = HoldoutSpec(
        symbols=args.symbols,
        timeframe="1h",
        holdout_start_dt=datetime.strptime(args.start, "%Y-%m-%d"),
        holdout_end_dt=datetime.strptime(args.end, "%Y-%m-%d"),
        warmup_days=args.warmup_days,
    )
    print(
        f"Holdout: {args.start} -> {args.end}  "
        f"Warmup: {args.warmup_days}d  "
        f"Symbols: {', '.join(spec.symbols)}"
    )

    cfg = load_config()
    db = DBManager(cfg.app.db_path)

    factories = [
        ("DonchianFixedRR",            lambda: DonchianFixedRR()),
        ("DonchianFixedRRTrendFilter", lambda: DonchianFixedRRTrendFilter()),
    ]
    results = run_strategies_on_holdout(factories, spec, db)

    verdict = judge_variant_vs_baseline(
        variant_name="DonchianFixedRRTrendFilter",
        variant_result=results["DonchianFixedRRTrendFilter"],
        baseline_name="DonchianFixedRR",
        baseline_result=results["DonchianFixedRR"],
    )
    print()
    print(format_verdict_line(verdict))
    for reason in verdict.reasons:
        print(f"    -> {reason}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "results.json").write_text(
        json.dumps(results, indent=2, default=str),
        encoding="utf-8",
    )
    (args.out_dir / "verdict.json").write_text(
        json.dumps(verdict.to_dict(), indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved {args.out_dir / 'results.json'}")
    print(f"Saved {args.out_dir / 'verdict.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
