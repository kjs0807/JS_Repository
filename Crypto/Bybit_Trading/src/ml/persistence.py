"""Run artifact persistence: model.joblib + meta.json + report.json (separated)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import joblib


@dataclass
class RunArtifact:
    model: Any
    meta: Dict[str, Any]
    report: Dict[str, Any]
    run_dir: Path


def save_run(
    run_dir: Path,
    model: Any,
    meta: Dict[str, Any],
    report: Dict[str, Any],
) -> None:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, run_dir / "model.joblib")
    with (run_dir / "meta.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)
    with (run_dir / "report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)


def load_run(run_dir: Path) -> RunArtifact:
    run_dir = Path(run_dir)
    model = joblib.load(run_dir / "model.joblib")
    with (run_dir / "meta.json").open("r", encoding="utf-8") as f:
        meta = json.load(f)
    with (run_dir / "report.json").open("r", encoding="utf-8") as f:
        report = json.load(f)
    return RunArtifact(model=model, meta=meta, report=report, run_dir=run_dir)


__all__ = ["RunArtifact", "save_run", "load_run"]
