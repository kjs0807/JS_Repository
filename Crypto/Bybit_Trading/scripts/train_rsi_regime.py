"""Build + label a daily RSI divergence regime dataset.

Research track (protocol §P9). NOT a trade-level signal — do not wire
anything from ``src/research/regime/`` into live strategy modules.

Usage::

    python -m scripts.train_rsi_regime
    python -m scripts.train_rsi_regime --symbol BTCUSDT --out-dir logs/research/rsi_regime

Pipeline:
1. Load daily OHLCV from DB (default: BTCUSDT 2021-01-01 ~ latest)
2. Compute RSI(14), ATR(14)%, RSI z-score, 100d trend
3. Scan for confirmed RSI divergence events (reusing
   ``src.ml.helpers.divergence.detect_divergence``)
4. Attach forward-horizon log returns + regime labels
5. Save events to ``logs/research/rsi_regime/events.parquet`` (or CSV)
   and unconditional stats to ``unconditional.json``

``scripts/evaluate_rsi_regime.py`` consumes those artifacts.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import load_config
from src.data_manager.db import DBManager
from src.research.regime.divergence_events import (
    BuildConfig,
    build_divergence_events,
)
from src.research.regime.regime_labels import (
    LabelConfig,
    attach_forward_labels,
    compute_unconditional_stats,
)


def _date_to_ms(s: str) -> int:
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _load_daily(
    db: DBManager, symbol: str, start_ms: Optional[int],
    end_ms: Optional[int],
) -> pd.DataFrame:
    df = db.get_bars(symbol, "1d", start_time=start_ms, end_time=end_ms)
    return df.sort_values("open_time").reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build daily RSI divergence regime dataset.",
    )
    parser.add_argument("--symbol", type=str, default="BTCUSDT")
    parser.add_argument("--start", type=str, default="2021-01-01")
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument(
        "--horizons", type=int, nargs="*", default=[20, 40, 60],
    )
    parser.add_argument("--k-sigma", type=float, default=0.5)
    parser.add_argument("--confirmation-bars", type=int, default=3)
    parser.add_argument("--lookback-bars", type=int, default=30)
    parser.add_argument(
        "--out-dir", type=Path,
        default=PROJECT_ROOT / "logs" / "research" / "rsi_regime",
    )
    args = parser.parse_args()

    cfg = load_config()
    db = DBManager(cfg.app.db_path)

    start_ms = _date_to_ms(args.start) if args.start else None
    end_ms = _date_to_ms(args.end) if args.end else None

    df = _load_daily(db, args.symbol, start_ms, end_ms)
    print(f"Loaded {args.symbol} daily: n={len(df)}")
    if df.empty:
        print("ERROR: no daily rows in DB for requested range")
        return 1
    first_dt = datetime.fromtimestamp(
        df["open_time"].iloc[0] / 1000, timezone.utc,
    ).strftime("%Y-%m-%d")
    last_dt = datetime.fromtimestamp(
        df["open_time"].iloc[-1] / 1000, timezone.utc,
    ).strftime("%Y-%m-%d")
    print(f"Range: {first_dt} .. {last_dt}")

    build_cfg = BuildConfig(
        confirmation_bars=args.confirmation_bars,
        lookback_bars=args.lookback_bars,
    )
    events = build_divergence_events(df, args.symbol, build_cfg)
    print(f"Divergence events detected: {len(events)}")
    if not events.empty:
        print("  by type:")
        for dt, n in events["div_type"].value_counts().items():
            print(f"    {dt}: {n}")

    close = df["close"].to_numpy(dtype=float)
    uncond = compute_unconditional_stats(close, tuple(args.horizons))
    print("Unconditional stats (full series):")
    for h, u in uncond.items():
        print(
            f"  horizon={h}: mean={u.mean:+.4f} std={u.std:.4f} n={u.n}"
        )

    label_cfg = LabelConfig(horizons=tuple(args.horizons), k_sigma=args.k_sigma)
    events, _ = attach_forward_labels(
        events, close, label_cfg, uncond,
    )
    print(f"Events after horizon trim: {len(events)}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    events_path = args.out_dir / "events.csv"
    events.to_csv(events_path, index=False)
    uncond_path = args.out_dir / "unconditional.json"
    uncond_path.write_text(
        json.dumps(
            {
                "symbol": args.symbol,
                "first_date": first_dt,
                "last_date": last_dt,
                "horizons": list(args.horizons),
                "k_sigma": args.k_sigma,
                "confirmation_bars": args.confirmation_bars,
                "lookback_bars": args.lookback_bars,
                "stats": {
                    str(h): {"mean": u.mean, "std": u.std, "n": u.n}
                    for h, u in uncond.items()
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved events -> {events_path}")
    print(f"Saved uncond -> {uncond_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
