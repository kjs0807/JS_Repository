"""Rule-based experiment orchestrator.

Single entry point that runs the ``D2-core -> D2-grid -> BBKC-universe``
pipeline sequentially on the shared holdout window. Each stage lives in
its own script (see ``d2_core_eval.py``, ``d2_grid.py``,
``bbkc_universe_eval.py``); this wrapper exists so the user never has to
remember "what do I run first?".

Stage order is fixed on purpose:

    1. d2_core_eval      -- cheap, validates the baseline+variant pair
    2. bbkc_universe_eval -- cheap, independent of d2
    3. d2_grid           -- expensive (hundreds of cells), skipped by
                            default and opted in with --with-grid

Each stage writes to its own directory under ``logs/`` so results never
overlap, and a manifest file ``logs/rule_based_manifest.json`` records
which stages finished (with timestamps) so downstream reports can cite
exact paths.

This orchestrator intentionally does not re-implement any evaluation
logic. It is a sequencer. If a stage fails, the manifest records the
failure and the later stages still run (they are independent).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_stage(
    label: str,
    cmd: List[str],
    logs_dir: Path,
) -> Dict[str, Any]:
    t0 = time.time()
    log_path = logs_dir / f"{label}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\n=== stage: {label} ===")
    print(f"    cmd : {' '.join(cmd)}")
    print(f"    log : {log_path}")
    try:
        with log_path.open("w", encoding="utf-8") as f:
            result = subprocess.run(
                cmd, stdout=f, stderr=subprocess.STDOUT, check=False,
                cwd=str(PROJECT_ROOT),
            )
        ok = result.returncode == 0
        status = "OK" if ok else f"FAIL(returncode={result.returncode})"
    except Exception as exc:
        ok = False
        status = f"EXCEPTION({exc})"
    elapsed = round(time.time() - t0, 1)
    print(f"    -> {status}  ({elapsed}s)")
    return {
        "label": label,
        "cmd": cmd,
        "ok": ok,
        "status": status,
        "elapsed_s": elapsed,
        "log_path": str(log_path),
        "finished_at": datetime.utcnow().isoformat() + "Z",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rule-based experiment orchestrator.",
    )
    parser.add_argument(
        "--with-grid", action="store_true",
        help="Also run the D2 parameter grid (slow, ~10min+).",
    )
    parser.add_argument(
        "--grid-max-cells", type=int, default=0,
        help="Cap the D2 grid at N cells (for smoke testing).",
    )
    parser.add_argument(
        "--skip-d2-core", action="store_true",
        help="Skip the d2_core_eval stage.",
    )
    parser.add_argument(
        "--skip-bbkc-universe", action="store_true",
        help="Skip the bbkc_universe_eval stage.",
    )
    parser.add_argument(
        "--logs-dir", type=Path,
        default=PROJECT_ROOT / "logs" / "rule_based_runner",
    )
    parser.add_argument("--start", type=str, default="2025-10-01")
    parser.add_argument("--end", type=str, default="2026-04-10")
    args = parser.parse_args()

    manifest: Dict[str, Any] = {
        "started_at": datetime.utcnow().isoformat() + "Z",
        "holdout": {"start": args.start, "end": args.end},
        "stages": [],
    }
    args.logs_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_d2_core:
        manifest["stages"].append(_run_stage(
            label="d2_core_eval",
            cmd=[
                sys.executable, "-m", "scripts.d2_core_eval",
                "--start", args.start, "--end", args.end,
            ],
            logs_dir=args.logs_dir,
        ))

    if not args.skip_bbkc_universe:
        manifest["stages"].append(_run_stage(
            label="bbkc_universe_eval",
            cmd=[
                sys.executable, "-m", "scripts.bbkc_universe_eval",
                "--start", args.start, "--end", args.end,
            ],
            logs_dir=args.logs_dir,
        ))

    if args.with_grid:
        grid_cmd = [
            sys.executable, "-m", "scripts.d2_grid",
            "--start", args.start, "--end", args.end,
        ]
        if args.grid_max_cells > 0:
            grid_cmd += ["--max-cells", str(args.grid_max_cells)]
        manifest["stages"].append(_run_stage(
            label="d2_grid",
            cmd=grid_cmd,
            logs_dir=args.logs_dir,
        ))

    # Re-run holdout_verdict on the round1 artifact so the manifest
    # also contains the baseline round1 verdicts in one place.
    round1_results = PROJECT_ROOT / "logs" / "variant_round1" / "results.json"
    if round1_results.exists():
        manifest["stages"].append(_run_stage(
            label="round1_holdout_verdict",
            cmd=[
                sys.executable, "-m", "scripts.holdout_verdict",
                str(round1_results),
                "--auto-pairs",
                "--out", str(
                    PROJECT_ROOT / "logs" / "rule_based_runner"
                    / "round1_verdict.md"
                ),
                "--out-json", str(
                    PROJECT_ROOT / "logs" / "rule_based_runner"
                    / "round1_verdict.json"
                ),
            ],
            logs_dir=args.logs_dir,
        ))

    manifest["finished_at"] = datetime.utcnow().isoformat() + "Z"
    manifest_path = PROJECT_ROOT / "logs" / "rule_based_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    print(f"\nManifest: {manifest_path}")

    any_failed = any(not s["ok"] for s in manifest["stages"])
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
