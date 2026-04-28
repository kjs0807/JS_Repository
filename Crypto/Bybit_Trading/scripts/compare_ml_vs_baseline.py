"""Bar-level comparator CLI for filter-type ML patterns.

Runs the raw baseline strategy and the ML-wrapped variant through
``BacktestEngine`` on the same holdout window and emits a
``BAR_FILTER_*`` verdict using
``src/evaluation/bar_level_comparison.py``.

This is the D2 comparator from the design memo
(``docs/superpowers/specs/ml/2026-04-14_d2_bar_level_baseline_verdict_design.md``).
D1 event-level ``filter_verdict`` lives in ``src/ml/validator.py`` and is
attached to every training run. D2 is a post-processing step that
answers the deployment question for filter-type patterns.

Scope (first version)
---------------------
Currently the script only supports rule-based "pre-built" filter
wrappers registered in ``FILTER_WRAPPER_REGISTRY``. Loading an ML
artifact (train_ml_pattern output) as the ML arm is deferred — when a
new filter-type pattern gets trained, register its factory here and the
comparator will use it. The current repo has no live ML filter left
(BBKC filter is KILL), but the scaffolding is in place so future filter
experiments can be judged without re-implementing BacktestEngine
plumbing.

Usage::

    python -m scripts.compare_ml_vs_baseline \\
        --pattern bbkc_filter \\
        --out logs/bar_level_comparison/bbkc_filter.json

If ``--pattern`` is not registered the script prints a
"not applicable" message and exits with code 0 (this is not a failure —
standalone patterns like RSI divergence are not comparable at bar
level).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import load_config
from src.data_manager.db import DBManager
from src.evaluation.bar_level_comparison import compare_ml_vs_baseline
from src.evaluation.holdout import HoldoutSpec


# --- Baseline + ML wrapper registry -------------------------------------
#
# Each entry says: "for pattern X, the baseline is strategy A() and the
# ML wrapper is strategy B()." Both entries are **factory callables** so
# BacktestEngine gets a fresh strategy per symbol. The ML wrapper must
# be a strategy class (or subclass) that reads an artifact and gates
# entries the same way the baseline enters.
#
# For the current repo the only filter-type pattern we ever trained was
# bbkc_filter (KILL), and PatternMLFilterStrategy was its wrapper. It
# is wired up with ``_bbkc_ml_factory`` below, but only when a live
# artifact path is passed on the CLI; otherwise the comparator would
# compare BBKCSqueeze with itself and emit BAR_FILTER_NEUTRAL, which is
# misleading.

def _import_bbkc() -> Callable[[], Any]:
    from src.strategies.bbkc_squeeze import BBKCSqueeze
    return lambda: BBKCSqueeze()


def _make_ml_factory_from_artifact(
    pattern_name: str, artifact_dir: Path,
) -> Callable[[], Any]:
    """Build an ML-wrapper factory from a train_ml_pattern artifact."""
    from src.strategies.pattern_ml_filter import PatternMLFilterStrategy

    def factory() -> Any:
        return PatternMLFilterStrategy.from_artifact(
            artifact_dir=artifact_dir,
            pattern_name=pattern_name,
        )

    return factory


FILTER_WRAPPER_REGISTRY: Dict[str, Dict[str, Any]] = {
    "bbkc_filter": {
        "baseline_name": "BBKCSqueeze",
        "baseline_factory": _import_bbkc,
        "ml_wrapper_name": "PatternMLFilterStrategy[bbkc_filter]",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bar-level filter-type pattern comparator (D2).",
    )
    parser.add_argument(
        "--pattern", required=True, type=str,
        help="Filter-type pattern name (e.g. 'bbkc_filter').",
    )
    parser.add_argument(
        "--artifact-dir", type=Path, default=None,
        help="Path to trained ML artifact directory. Required only "
             "when --pattern is a registered filter with an ML arm.",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output JSON path (defaults to "
             "logs/bar_level_comparison/<pattern>.json)",
    )
    parser.add_argument(
        "--start", type=str, default="2025-10-01",
        help="Holdout start (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end", type=str, default="2026-04-10",
        help="Holdout end (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--warmup-days", type=int, default=14,
        help="Days before --start the feed begins.",
    )
    parser.add_argument(
        "--symbols", nargs="*",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"],
    )
    parser.add_argument(
        "--min-trades", type=int, default=5,
        help="Minimum trades per arm for the verdict to be emitted.",
    )
    args = parser.parse_args()

    if args.pattern not in FILTER_WRAPPER_REGISTRY:
        print(
            f"[compare_ml_vs_baseline] bar-level comparison not "
            f"applicable for pattern='{args.pattern}': "
            f"not in FILTER_WRAPPER_REGISTRY. "
            f"D1 event-level filter_verdict is sufficient for standalone "
            f"patterns."
        )
        return 0

    entry = FILTER_WRAPPER_REGISTRY[args.pattern]
    if args.artifact_dir is None:
        print(
            f"[compare_ml_vs_baseline] --artifact-dir is required for "
            f"pattern='{args.pattern}' (the ML arm is a live artifact)."
        )
        return 1
    if not args.artifact_dir.exists():
        print(
            f"[compare_ml_vs_baseline] artifact dir not found: "
            f"{args.artifact_dir}"
        )
        return 1

    baseline_factory = entry["baseline_factory"]()  # call to resolve lazily
    ml_factory = _make_ml_factory_from_artifact(
        args.pattern, args.artifact_dir,
    )

    from datetime import datetime
    spec = HoldoutSpec(
        symbols=args.symbols,
        timeframe="1h",
        holdout_start_dt=datetime.strptime(args.start, "%Y-%m-%d"),
        holdout_end_dt=datetime.strptime(args.end, "%Y-%m-%d"),
        warmup_days=args.warmup_days,
    )

    cfg = load_config()
    db = DBManager(cfg.app.db_path)

    comparison = compare_ml_vs_baseline(
        baseline_strategy_name=entry["baseline_name"],
        baseline_factory=baseline_factory,
        ml_wrapper_name=entry["ml_wrapper_name"],
        ml_factory=ml_factory,
        spec=spec,
        db=db,
        min_trades_for_judgement=args.min_trades,
    )

    out_path = args.out or (
        PROJECT_ROOT / "logs" / "bar_level_comparison" /
        f"{args.pattern}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(comparison.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )

    print()
    print(f"=== BarLevelComparison: {args.pattern} ===")
    print(
        f"  RAW    n={comparison.raw.n_trades:4d} "
        f"pnl={comparison.raw.total_pnl:+10.2f} "
        f"wr={comparison.raw.win_rate:5.1%} "
        f"avg={comparison.raw.avg_trade_pnl:+8.2f} "
        f"mdd={comparison.raw.max_drawdown:5.1%}"
    )
    print(
        f"  ML     n={comparison.ml.n_trades:4d} "
        f"pnl={comparison.ml.total_pnl:+10.2f} "
        f"wr={comparison.ml.win_rate:5.1%} "
        f"avg={comparison.ml.avg_trade_pnl:+8.2f} "
        f"mdd={comparison.ml.max_drawdown:5.1%}"
    )
    print(
        f"  DELTA  dTrades={comparison.delta_trade_count:+4d} "
        f"dPnl={comparison.delta_total_pnl:+10.2f} "
        f"dWR={comparison.delta_win_rate:+6.1%} "
        f"dAvg={comparison.delta_avg_trade_pnl:+8.2f}"
    )
    print(f"  VERDICT: {comparison.bar_level_filter_verdict}")
    print(f"  Saved:  {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
