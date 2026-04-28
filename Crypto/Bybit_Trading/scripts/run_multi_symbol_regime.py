"""Run RSI regime train+evaluate across multiple symbols.

Research-only (protocol §P9). Wraps ``train_rsi_regime`` and
``evaluate_rsi_regime`` to produce per-symbol artifacts and a
cross-asset aggregate report. The aggregate is what the GO/NO-GO
escalation will read when deciding whether to revisit the connection
to strategies.

Usage::

    python -m scripts.run_multi_symbol_regime
    python -m scripts.run_multi_symbol_regime --symbols BTCUSDT ETHUSDT

Artifacts::

    logs/research/rsi_regime_multi/
      ├── <symbol>/events.csv
      ├── <symbol>/unconditional.json
      ├── <symbol>/report.json
      ├── <symbol>/report.md
      ├── <symbol>/cross_window_lifts.json
      └── cross_asset_summary.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"]


def _run(cmd: List[str]) -> int:
    print(" ".join(cmd))
    return subprocess.run(cmd, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Multi-symbol RSI regime research driver.",
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--start", type=str, default="2021-01-01")
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument(
        "--root-out-dir", type=Path,
        default=PROJECT_ROOT / "logs" / "research" / "rsi_regime_multi",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Skip symbols whose events.csv already exists",
    )
    args = parser.parse_args()

    args.root_out_dir.mkdir(parents=True, exist_ok=True)
    summary: Dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "symbols": args.symbols,
        "per_symbol": {},
    }

    for sym in args.symbols:
        sym_dir = args.root_out_dir / sym
        sym_dir.mkdir(parents=True, exist_ok=True)
        events_path = sym_dir / "events.csv"
        if args.skip_existing and events_path.exists():
            print(f"[skip-existing] {sym}")
        else:
            train_cmd = [
                sys.executable, "-m", "scripts.train_rsi_regime",
                "--symbol", sym,
                "--start", args.start,
                "--out-dir", str(sym_dir),
            ]
            if args.end:
                train_cmd += ["--end", args.end]
            rc = _run(train_cmd)
            if rc != 0:
                summary["per_symbol"][sym] = {"ok": False, "stage": "train"}
                continue
        eval_cmd = [
            sys.executable, "-m", "scripts.evaluate_rsi_regime",
            "--out-dir", str(sym_dir),
        ]
        rc = _run(eval_cmd)
        if rc != 0:
            summary["per_symbol"][sym] = {"ok": False, "stage": "evaluate"}
            continue

        # Aggregate: read cross_window_lifts.json + key report fields
        strong_path = sym_dir / "cross_window_lifts.json"
        report_path = sym_dir / "report.json"
        strong = json.loads(strong_path.read_text(encoding="utf-8")) if strong_path.exists() else []
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        summary["per_symbol"][sym] = {
            "ok": True,
            "n_events_total": report.get("n_events_total", 0),
            "n_events_is": report.get("n_events_is", 0),
            "n_events_oos": report.get("n_events_oos", 0),
            "strong_lifts": strong,
            "strong_lifts_count": len(strong),
        }

    # Cross-asset: pool strong lifts that appear in >= 2 symbols
    pooled: Dict[str, List[str]] = {}
    for sym, d in summary["per_symbol"].items():
        if not d.get("ok"):
            continue
        for s in d.get("strong_lifts", []):
            key = f"h{s['horizon']}/{s['div_type']}/{s['regime']}"
            pooled.setdefault(key, []).append(sym)
    cross_asset = [
        {"signature": k, "symbols": v}
        for k, v in pooled.items()
        if len(v) >= 2
    ]
    summary["cross_asset_strong"] = cross_asset
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()

    summary_path = args.root_out_dir / "cross_asset_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2), encoding="utf-8",
    )

    print()
    print("=== multi-symbol summary ===")
    for sym, d in summary["per_symbol"].items():
        if d.get("ok"):
            print(
                f"  {sym:10s} n={d['n_events_total']:4d} "
                f"IS={d['n_events_is']:4d} OOS={d['n_events_oos']:4d} "
                f"strong={d['strong_lifts_count']}"
            )
        else:
            print(f"  {sym:10s} FAIL at {d.get('stage')}")
    print()
    print("=== cross-asset strong lifts (in >= 2 symbols) ===")
    if not cross_asset:
        print("  (none)")
    for row in cross_asset:
        print(f"  {row['signature']}: {row['symbols']}")

    print(f"\nSaved {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
