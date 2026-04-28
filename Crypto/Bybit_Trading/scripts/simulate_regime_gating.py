"""Research-only gating simulation driver.

Loads a symbol's events.csv (from ``train_rsi_regime.py``), applies
the default BTC research gating rules (or a custom policy file), and
prints / saves a gated vs unconditional comparison per horizon.

This does NOT run a backtest and does NOT touch any strategy. It only
reports on the forward-horizon return distribution of the gated
subset. The purpose is to answer:

    "If a future strategy were to gate its entries by RSI regime
    state, does the sum of forward returns look materially different
    from doing nothing?"

Not answering the question: "does this turn into live money". That
requires bar-level comparator + real strategy connection, which is
forbidden until protocol §P9 conditions are met.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import load_config
from src.data_manager.db import DBManager
from src.research.regime.gating_eval import (
    DEFAULT_GATING_RULES_BTC_RESEARCH,
    GatingRule,
    simulate_gating,
)


def _load_events(events_path: Path) -> pd.DataFrame:
    if not events_path.exists():
        raise FileNotFoundError(f"events file not found: {events_path}")
    return pd.read_csv(events_path)


def _load_close_for_symbol(symbol: str, uncond_meta: Dict[str, Any]) -> np.ndarray:
    cfg = load_config()
    db = DBManager(cfg.app.db_path)
    start_ms = int(
        datetime.strptime(uncond_meta["first_date"], "%Y-%m-%d")
        .replace(tzinfo=timezone.utc).timestamp() * 1000
    )
    end_ms = int(
        datetime.strptime(uncond_meta["last_date"], "%Y-%m-%d")
        .replace(tzinfo=timezone.utc).timestamp() * 1000
    ) + 86_400_000
    df = db.get_bars(symbol, "1d", start_time=start_ms, end_time=end_ms)
    df = df.sort_values("open_time").reset_index(drop=True)
    return df["close"].to_numpy(dtype=float)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RSI regime gating simulation (research-only).",
    )
    parser.add_argument(
        "--events-dir", type=Path,
        default=PROJECT_ROOT / "logs" / "research" / "rsi_regime",
        help="Directory containing events.csv + unconditional.json",
    )
    parser.add_argument(
        "--horizons", type=int, nargs="*", default=[20, 40, 60],
    )
    parser.add_argument(
        "--policy", type=Path, default=None,
        help="Optional JSON policy file: [{\"div_type\":...,\"horizon\":...,\"direction\":...}]",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Optional output JSON path",
    )
    args = parser.parse_args()

    events = _load_events(args.events_dir / "events.csv")
    uncond_meta = json.loads(
        (args.events_dir / "unconditional.json").read_text(encoding="utf-8"),
    )
    symbol = uncond_meta["symbol"]
    close = _load_close_for_symbol(symbol, uncond_meta)

    rules: List[GatingRule]
    if args.policy is not None:
        policy_raw = json.loads(args.policy.read_text(encoding="utf-8"))
        rules = [
            GatingRule(
                div_type=p["div_type"],
                horizon=int(p["horizon"]),
                direction=p["direction"],
            )
            for p in policy_raw
        ]
        policy_source = str(args.policy)
    else:
        rules = DEFAULT_GATING_RULES_BTC_RESEARCH
        policy_source = "DEFAULT_GATING_RULES_BTC_RESEARCH"

    print(f"Symbol: {symbol}  events={len(events)}  close_n={len(close)}")
    print(f"Policy source: {policy_source}")
    print()

    results: List[Dict[str, Any]] = []
    for h in args.horizons:
        res = simulate_gating(
            events=events, horizon=h, rules=rules, baseline_close=close,
        )
        results.append(res.to_dict())
        print(f"--- horizon={h} ---")
        print(
            f"  unconditional: n={res.unconditional.n_events} "
            f"mean={res.unconditional.mean_fwd:+.4f} "
            f"std={res.unconditional.std_fwd:.4f} "
            f"sharpe={res.unconditional.sharpe:+.2f} "
            f"win_rate={res.unconditional.win_rate:.1%}"
        )
        print(
            f"  gated:         n={res.gated.n_events:4d} "
            f"mean={res.gated.mean_fwd:+.4f} "
            f"std={res.gated.std_fwd:.4f} "
            f"sharpe={res.gated.sharpe:+.2f} "
            f"win_rate={res.gated.win_rate:.1%}"
        )
        for label, stats in res.per_rule.items():
            print(
                f"    {label:30s} n={stats.n_events:3d} "
                f"mean={stats.mean_fwd:+.4f} "
                f"sharpe={stats.sharpe:+.2f} "
                f"win={stats.win_rate:.1%}"
            )
        print()

    out_path = args.out or (args.events_dir / "gating_simulation.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "symbol": symbol,
                "policy_source": policy_source,
                "rules": [
                    {"div_type": r.div_type, "horizon": r.horizon,
                     "direction": r.direction}
                    for r in rules
                ],
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
