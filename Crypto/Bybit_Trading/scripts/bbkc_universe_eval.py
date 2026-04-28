"""BBKC-universe experiment: same BBKCSqueeze, different symbol sets.

Intent
------
BBKCSqueeze itself is the only robust live strategy. Round 1 already
showed that touching its entry or exit logic destroys more edge than
it adds (BBKCSqueezeHTFTrend KILL). The next high-value lever is
**universe** — keep the strategy untouched, but change which symbols
it trades.

Round 1 per-symbol holdout numbers (`logs/variant_round1/results.json`):

    BTCUSDT   : +$1616  (Sharpe 3.09)  PASS
    ETHUSDT   : +$2182  (Sharpe 2.99)  PASS
    SOLUSDT   :  -$720  (Sharpe -3.74) FAIL
    LINKUSDT  : -$1158  (Sharpe -5.46) FAIL
    AVAXUSDT  : +$1780  (Sharpe 2.77)  PASS

The three winners (BTC/ETH/AVAX) carry almost all of the edge.
SOL/LINK are structural underperformers on this strategy and window.

Risks and safety rails
----------------------
"Pick the best symbols" is trivially over-fit. We guard against that
by running only **5 pre-declared universes** (no search), each chosen
with an ex-ante reason documented in this file:

    ALL5   : the baseline universe (5 symbols, everything)
    BTCETH : BTC+ETH — the two deepest-liquidity coins
    BIGTHREE : BTC+ETH+AVAX — the PASS set from round 1
    EXCLUDE_SOL  : drop SOL only
    EXCLUDE_SOL_LINK : drop both structural failers

These are not "tuned" — they are 5 pre-committed hypotheses. The
verdict compares each subset to ALL5 using ``judge_variant_vs_baseline``
so promote/kill rules apply unchanged.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import load_config
from src.data_manager.db import DBManager
from src.evaluation.holdout import HoldoutSpec, run_strategies_on_holdout
from src.evaluation.verdict import (
    format_verdict_line,
    judge_variant_vs_baseline,
)
from src.strategies.bbkc_squeeze import BBKCSqueeze


PRE_DECLARED_UNIVERSES: Dict[str, List[str]] = {
    "ALL5":         ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"],
    "BTCETH":       ["BTCUSDT", "ETHUSDT"],
    "BIGTHREE":     ["BTCUSDT", "ETHUSDT", "AVAXUSDT"],
    "EXCLUDE_SOL":  ["BTCUSDT", "ETHUSDT", "LINKUSDT", "AVAXUSDT"],
    "EXCLUDE_SOL_LINK": ["BTCUSDT", "ETHUSDT", "AVAXUSDT"],
}


def _run_universe(
    label: str, symbols: List[str],
    holdout_start_dt: datetime, holdout_end_dt: datetime,
    warmup_days: int, db: Any,
) -> Dict[str, Any]:
    spec = HoldoutSpec(
        symbols=symbols,
        timeframe="1h",
        holdout_start_dt=holdout_start_dt,
        holdout_end_dt=holdout_end_dt,
        warmup_days=warmup_days,
    )
    results = run_strategies_on_holdout(
        [(f"BBKCSqueeze[{label}]", lambda: BBKCSqueeze())],
        spec,
        db,
    )
    return results[f"BBKCSqueeze[{label}]"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BBKCSqueeze universe-subset comparator.",
    )
    parser.add_argument(
        "--out-dir", type=Path,
        default=PROJECT_ROOT / "logs" / "bbkc_universe",
    )
    parser.add_argument("--start", type=str, default="2025-10-01")
    parser.add_argument("--end", type=str, default="2026-04-10")
    parser.add_argument("--warmup-days", type=int, default=14)
    args = parser.parse_args()

    holdout_start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    holdout_end_dt = datetime.strptime(args.end, "%Y-%m-%d")
    print(
        f"Holdout: {args.start} -> {args.end}  "
        f"Warmup: {args.warmup_days}d"
    )

    cfg = load_config()
    db = DBManager(cfg.app.db_path)

    results: Dict[str, Any] = {}
    for label, symbols in PRE_DECLARED_UNIVERSES.items():
        print(f"\n=== Universe: {label} ({symbols}) ===")
        results[label] = _run_universe(
            label, symbols,
            holdout_start_dt, holdout_end_dt, args.warmup_days,
            db,
        )

    baseline = results["ALL5"]
    verdicts = []
    print()
    for label in PRE_DECLARED_UNIVERSES:
        if label == "ALL5":
            continue
        v = judge_variant_vs_baseline(
            variant_name=f"BBKCSqueeze[{label}]",
            variant_result=results[label],
            baseline_name="BBKCSqueeze[ALL5]",
            baseline_result=baseline,
        )
        verdicts.append(v)
        print(format_verdict_line(v))
        for reason in v.reasons:
            print(f"    -> {reason}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "results.json").write_text(
        json.dumps(results, indent=2, default=str),
        encoding="utf-8",
    )
    (args.out_dir / "verdicts.json").write_text(
        json.dumps([v.to_dict() for v in verdicts], indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved {args.out_dir / 'results.json'}")
    print(f"Saved {args.out_dir / 'verdicts.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
