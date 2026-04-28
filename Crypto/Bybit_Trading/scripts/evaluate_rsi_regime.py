"""Evaluate RSI divergence events as a daily regime signal.

Reads artifacts from ``scripts/train_rsi_regime.py`` output and
produces:

- ``logs/research/rsi_regime/report.json`` — full per-type lift table
- ``logs/research/rsi_regime/report.md``  — human-readable summary
- Terminal print of IS / OOS top liftable types

Descriptive only. No model. No trade-level connection. See
``docs/superpowers/specs/experiments/2026-04-14_rsi_regime_research_problem.md``.
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
from src.research.regime.evaluator import (
    DIV_TYPES,
    REGIME_VALUES,
    evaluate_rsi_regime,
)
from src.research.regime.regime_labels import (
    UnconditionalStats,
    compute_unconditional_stats,
)


def _load_events(events_path: Path) -> pd.DataFrame:
    if not events_path.exists():
        raise FileNotFoundError(f"events file not found: {events_path}")
    return pd.read_csv(events_path)


def _load_unconditional(uncond_path: Path) -> Dict[str, Any]:
    return json.loads(uncond_path.read_text(encoding="utf-8"))


def _format_row(row: Any) -> str:
    return (
        f"  {row.split:3s} h={row.horizon:2d} {row.div_type:14s} "
        f"n={row.n_events:4d} "
        f"down={row.down_pct:5.1%} flat={row.flat_pct:5.1%} up={row.up_pct:5.1%} "
        f"lift(D/F/U)={row.lift_down:4.2f}/{row.lift_flat:4.2f}/{row.lift_up:4.2f}"
    )


def _write_markdown(
    out_path: Path, report_dict: Dict[str, Any], symbol: str,
    events_count: int, date_range: str,
) -> None:
    lines = [
        f"# RSI Divergence Regime Report — {symbol}",
        "",
        f"**Dataset**: {events_count} events ({date_range})",
        f"**Horizons**: {report_dict['horizons']}",
        f"**IS ratio**: {report_dict['is_ratio']}",
        f"**IS events**: {report_dict['n_events_is']}  "
        f"**OOS events**: {report_dict['n_events_oos']}",
        "",
        "## Base rates (unconditional regime class distribution)",
        "",
        "| Horizon | DOWN | FLAT | UP |",
        "|---|---|---|---|",
    ]
    for h_str, base in report_dict["base_rates"].items():
        lines.append(
            f"| {h_str} | {base['DOWN']:.1%} | {base['FLAT']:.1%} | "
            f"{base['UP']:.1%} |"
        )
    lines.extend([
        "",
        "## Per-type regime distribution + lift",
        "",
        "Lift = P(regime | div_type) / P(regime | base rate). "
        "Values near 1.0 = no information; > 1.2 or < 0.8 on a "
        "reasonable sample = directional signal.",
        "",
        "| Split | H | Type | n | P(DOWN) | P(FLAT) | P(UP) | Lift DOWN | Lift FLAT | Lift UP |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ])
    for row in report_dict["rows"]:
        dist = row["dist"]
        lift = row["lift"]
        lines.append(
            f"| {row['split']} | {row['horizon']} | {row['div_type']} | "
            f"{row['n_events']} | {dist['DOWN']:.1%} | {dist['FLAT']:.1%} | "
            f"{dist['UP']:.1%} | {lift['DOWN']:.2f} | {lift['FLAT']:.2f} | "
            f"{lift['UP']:.2f} |"
        )
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _cross_window_strong_lifts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Lift > 1.2 or < 0.8 in BOTH IS AND OOS for same (horizon, type)."""
    by_key: Dict[tuple, Dict[str, Dict[str, Any]]] = {}
    for r in rows:
        key = (r["horizon"], r["div_type"])
        by_key.setdefault(key, {})[r["split"]] = r
    out: List[Dict[str, Any]] = []
    for key, splits in by_key.items():
        if "IS" not in splits or "OOS" not in splits:
            continue
        is_r = splits["IS"]
        oos_r = splits["OOS"]
        if is_r["n_events"] < 20 or oos_r["n_events"] < 5:
            continue
        for regime in REGIME_VALUES:
            is_lift = is_r["lift"][regime]
            oos_lift = oos_r["lift"][regime]
            if (
                (is_lift > 1.2 and oos_lift > 1.2)
                or (is_lift < 0.8 and oos_lift < 0.8)
            ):
                out.append({
                    "horizon": key[0],
                    "div_type": key[1],
                    "regime": regime,
                    "is_lift": is_lift,
                    "oos_lift": oos_lift,
                    "is_n": is_r["n_events"],
                    "oos_n": oos_r["n_events"],
                })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate RSI divergence daily regime signal.",
    )
    parser.add_argument(
        "--out-dir", type=Path,
        default=PROJECT_ROOT / "logs" / "research" / "rsi_regime",
    )
    parser.add_argument("--is-ratio", type=float, default=0.8)
    args = parser.parse_args()

    events_path = args.out_dir / "events.csv"
    uncond_path = args.out_dir / "unconditional.json"
    events = _load_events(events_path)
    uncond_meta = _load_unconditional(uncond_path)
    symbol = uncond_meta["symbol"]
    horizons = uncond_meta["horizons"]
    k_sigma = float(uncond_meta["k_sigma"])

    # Re-load full close to compute base rates deterministically.
    cfg = load_config()
    db = DBManager(cfg.app.db_path)
    df = db.get_bars(
        symbol, "1d",
        start_time=datetime.strptime(
            uncond_meta["first_date"], "%Y-%m-%d",
        ).replace(tzinfo=timezone.utc).timestamp() * 1000,
        end_time=(
            datetime.strptime(
                uncond_meta["last_date"], "%Y-%m-%d",
            ).replace(tzinfo=timezone.utc).timestamp() * 1000 + 86_400_000
        ),
    )
    df = df.sort_values("open_time").reset_index(drop=True)
    close = df["close"].to_numpy(dtype=float)

    # Reconstruct UnconditionalStats objects (evaluator needs mean/std).
    unconditional_stats: Dict[int, Any] = {}
    for h in horizons:
        s = uncond_meta["stats"][str(h)]
        unconditional_stats[h] = UnconditionalStats(
            horizon=h, mean=s["mean"], std=s["std"], n=s["n"],
        )

    report = evaluate_rsi_regime(
        events=events,
        close=close,
        symbol=symbol,
        horizons=horizons,
        unconditional_stats=unconditional_stats,
        k_sigma=k_sigma,
        is_ratio=args.is_ratio,
    )

    report_dict = report.to_dict()
    report_path = args.out_dir / "report.json"
    report_path.write_text(
        json.dumps(report_dict, indent=2),
        encoding="utf-8",
    )

    # Pretty-print summary
    print(f"Symbol: {symbol}  events={report.n_events_total}")
    print(f"IS: {report.n_events_is}  OOS: {report.n_events_oos}")
    print()
    print("Base rates:")
    for h_str, base in report_dict["base_rates"].items():
        print(
            f"  h={h_str}: DOWN={base['DOWN']:5.1%} "
            f"FLAT={base['FLAT']:5.1%} UP={base['UP']:5.1%}"
        )
    print()
    print("Per-type rows:")
    for r in report.rows:
        print(_format_row(r))

    # Cross-window strong-lift check (GO/NO-GO evidence)
    strong = _cross_window_strong_lifts(report_dict["rows"])
    print()
    print("Cross-window STRONG lifts (IS AND OOS both > 1.2 or < 0.8):")
    if strong:
        for s in strong:
            print(
                f"  h={s['horizon']} type={s['div_type']} regime={s['regime']} "
                f"IS lift={s['is_lift']:.2f} (n={s['is_n']}) "
                f"OOS lift={s['oos_lift']:.2f} (n={s['oos_n']})"
            )
    else:
        print("  (none)")

    strong_path = args.out_dir / "cross_window_lifts.json"
    strong_path.write_text(json.dumps(strong, indent=2), encoding="utf-8")

    date_range = f"{uncond_meta['first_date']} .. {uncond_meta['last_date']}"
    md_path = args.out_dir / "report.md"
    _write_markdown(md_path, report_dict, symbol, len(events), date_range)

    print()
    print(f"Saved {report_path}")
    print(f"Saved {strong_path}")
    print(f"Saved {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
