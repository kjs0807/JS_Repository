"""Run bounded Beda+Bollinger strategy-mode experiments.

This search compares five families:

- BB re-entry with Beda as entry filter
- BB re-entry with Beda as exit/RSI context only
- BB breakout with Beda trend confirmation
- Contrarian Beda-start fade
- 1m BB re-entry with 5m Beda filter

The script intentionally records every evaluated run.  It does not stop at the
first weak result because the goal is comparing strategy families, not merely
finding one pass/fail candidate.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from backtester.core.config import BacktestConfig
from backtester.core.engine import BacktestEngine
from backtester.strategies.registry import build_strategy

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = {
    "1m": ROOT / "configs" / "beda_btcusdt_1m_sqlite.yaml",
    "5m": ROOT / "configs" / "beda_btcusdt_5m_sqlite.yaml",
}
OUT_DIR = ROOT / "runs" / "beda_modes_grid"
SUMMARY_CSV = OUT_DIR / "grid_summary.csv"
SUMMARY_JSON = OUT_DIR / "grid_summary.json"
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 5, 7, tzinfo=timezone.utc)
INITIAL_EQUITY = Decimal("50000")


def candidates(max_trials: int) -> list[dict[str, Any]]:
    common: dict[str, Any] = {
        "rsi_length": 13,
        "atr_period": 14,
        "slow_mult": 2.0,
        "fast_mult": 1.0,
        "bb_period": 20,
        "bb_std": 2.0,
        "allow_long": True,
        "allow_short": True,
        "leverage": "3",
        "margin_pct": "0.03",
    }
    mode_specs: list[tuple[str, list[dict[str, Any]]]] = [
        (
            "bb_reentry_beda_filter",
            [
                dict(reentry_lookback=4, long_rsi_min=48, short_rsi_max=52, take_profit_pct="0.004", stop_loss_pct="0.004", time_stop_bars=18),
                dict(reentry_lookback=6, long_rsi_min=50, short_rsi_max=50, take_profit_pct="0.006", stop_loss_pct="0.005", time_stop_bars=30),
                dict(reentry_lookback=8, long_rsi_min=52, short_rsi_max=48, take_profit_pct="0.008", stop_loss_pct="0.006", time_stop_bars=48),
                dict(bb_period=30, reentry_lookback=6, long_rsi_min=50, short_rsi_max=50, take_profit_pct="0.006", stop_loss_pct="0.005", time_stop_bars=36),
                dict(atr_period=10, slow_mult=1.7, fast_mult=0.8, reentry_lookback=5, take_profit_pct="0.005", stop_loss_pct="0.004", time_stop_bars=24),
                dict(allow_short=False, reentry_lookback=6, long_rsi_min=48, take_profit_pct="0.006", stop_loss_pct="0.005", time_stop_bars=36),
            ],
        ),
        (
            "bb_reentry_beda_exit",
            [
                dict(reentry_lookback=4, take_profit_pct="0.003", stop_loss_pct="0.004", time_stop_bars=12),
                dict(reentry_lookback=6, take_profit_pct="0.005", stop_loss_pct="0.005", time_stop_bars=24),
                dict(reentry_lookback=8, take_profit_pct="0.008", stop_loss_pct="0.006", time_stop_bars=48),
                dict(bb_period=30, reentry_lookback=6, take_profit_pct="0.006", stop_loss_pct="0.005", time_stop_bars=36),
                dict(allow_short=False, reentry_lookback=6, take_profit_pct="0.006", stop_loss_pct="0.005", time_stop_bars=36),
                dict(allow_long=False, reentry_lookback=6, take_profit_pct="0.006", stop_loss_pct="0.005", time_stop_bars=36),
            ],
        ),
        (
            "bb_breakout_beda_trend",
            [
                dict(long_rsi_min=52, short_rsi_max=48, take_profit_pct="0.006", stop_loss_pct="0.004", time_stop_bars=18),
                dict(long_rsi_min=55, short_rsi_max=45, take_profit_pct="0.010", stop_loss_pct="0.006", time_stop_bars=36),
                dict(bb_period=30, long_rsi_min=52, short_rsi_max=48, take_profit_pct="0.010", stop_loss_pct="0.006", time_stop_bars=48),
                dict(bb_std=2.5, long_rsi_min=52, short_rsi_max=48, take_profit_pct="0.012", stop_loss_pct="0.007", time_stop_bars=60),
                dict(atr_period=10, slow_mult=1.7, fast_mult=0.8, long_rsi_min=52, short_rsi_max=48, take_profit_pct="0.008", stop_loss_pct="0.005", time_stop_bars=30),
                dict(allow_short=False, long_rsi_min=50, take_profit_pct="0.010", stop_loss_pct="0.006", time_stop_bars=48),
            ],
        ),
        (
            "beda_start_contrarian",
            [
                dict(take_profit_pct="0.003", stop_loss_pct="0.003", time_stop_bars=10),
                dict(take_profit_pct="0.004", stop_loss_pct="0.004", time_stop_bars=18),
                dict(take_profit_pct="0.006", stop_loss_pct="0.005", time_stop_bars=30),
                dict(bb_period=30, take_profit_pct="0.005", stop_loss_pct="0.004", time_stop_bars=24),
                dict(atr_period=18, slow_mult=2.3, fast_mult=1.2, take_profit_pct="0.005", stop_loss_pct="0.004", time_stop_bars=24),
                dict(allow_long=False, take_profit_pct="0.004", stop_loss_pct="0.004", time_stop_bars=18),
            ],
        ),
        (
            "mtf_bb_reentry_beda_filter",
            [
                dict(reentry_lookback=4, filter_timeframe="5m", long_rsi_min=48, short_rsi_max=52, take_profit_pct="0.004", stop_loss_pct="0.004", time_stop_bars=18),
                dict(reentry_lookback=6, filter_timeframe="5m", long_rsi_min=50, short_rsi_max=50, take_profit_pct="0.006", stop_loss_pct="0.005", time_stop_bars=30),
                dict(reentry_lookback=8, filter_timeframe="5m", long_rsi_min=52, short_rsi_max=48, take_profit_pct="0.008", stop_loss_pct="0.006", time_stop_bars=48),
                dict(bb_period=30, reentry_lookback=6, filter_timeframe="5m", take_profit_pct="0.006", stop_loss_pct="0.005", time_stop_bars=36),
                dict(atr_period=10, slow_mult=1.7, fast_mult=0.8, reentry_lookback=5, filter_timeframe="5m", take_profit_pct="0.005", stop_loss_pct="0.004", time_stop_bars=24),
                dict(allow_short=False, reentry_lookback=6, filter_timeframe="5m", long_rsi_min=48, take_profit_pct="0.006", stop_loss_pct="0.005", time_stop_bars=36),
            ],
        ),
    ]
    rows: list[dict[str, Any]] = []
    for mode, specs in mode_specs:
        for spec in specs:
            params = {**common, **spec, "mode": mode}
            params.setdefault("long_rsi_take_profit", 65.0)
            params.setdefault("short_rsi_take_profit", 35.0)
            rows.append({"candidate": len(rows) + 1, "mode": mode, "params": params})
            if len(rows) >= max_trials:
                return rows
    return rows


def metrics_from_run(path: Path, tf: str) -> dict[str, float | int]:
    df = pl.read_parquet(path / "results" / "equity_curve.parquet")
    final = float(df["equity"][-1])
    total_return = final / float(INITIAL_EQUITY) - 1.0
    rets = (
        df.select((pl.col("equity") / pl.col("equity").shift(1) - 1.0).alias("r"))
        .drop_nulls()
        .filter(pl.col("r").is_finite())
    )
    mean = float(rets["r"].mean() or 0.0) if rets.height else 0.0
    std = float(rets["r"].std() or 0.0) if rets.height > 1 else 0.0
    minutes = 1 if tf == "1m" else 5
    periods_per_year = 365 * 24 * 60 / minutes
    sharpe = 0.0 if std == 0.0 else mean / std * math.sqrt(periods_per_year)
    max_eq = df["equity"].cum_max()
    dd = (df["equity"] / max_eq - 1.0).min()
    events = pl.scan_parquet(path / "events.parquet")
    fills = events.filter(pl.col("type") == "fill").select(pl.len()).collect().item()
    intents = (
        events.filter(pl.col("type") == "intent_created")
        .select(pl.len())
        .collect()
        .item()
    )
    return {
        "final_equity": final,
        "total_return": total_return,
        "sharpe": sharpe,
        "max_drawdown": float(dd or 0.0),
        "fills": int(fills),
        "intents": int(intents),
        "periods": int(df.height),
    }


def run_one(tf: str, item: dict[str, Any]) -> dict[str, Any]:
    base = BacktestConfig.from_yaml(CONFIGS[tf])
    mode = item["mode"]
    candidate = item["candidate"]
    run_tf = "1m_5m" if mode == "mtf_bb_reentry_beda_filter" else tf
    run_id = f"beda_modes_{candidate:02d}_{run_tf}"
    cfg_kwargs: dict[str, Any] = {
        "run_id": run_id,
        "start": START,
        "end": END,
        "output_dir": OUT_DIR,
        "persist_run_data": "none",
        "snapshot_every_bars": 60 if tf == "1m" else 12,
        "on_run_exists": "overwrite",
        "strategy_name": "beda_bollinger_modes",
        "strategy_params": item["params"],
    }
    if mode == "mtf_bb_reentry_beda_filter":
        cfg_kwargs["timeframes_per_symbol"] = {"BTCUSDT": ["1m", "5m"]}
        cfg_kwargs["primary_timeframe"] = "1m"
    cfg = replace(base, **cfg_kwargs)
    strategy = build_strategy(cfg.strategy_name, cfg.strategy_params)
    result = BacktestEngine(cfg, strategy, verbose=False).run()
    metrics = metrics_from_run(result.run_dir, tf)
    return {
        "candidate": candidate,
        "mode": mode,
        "tf": run_tf,
        "run_dir": str(result.run_dir),
        **metrics,
        "params": json.dumps(item["params"], sort_keys=True),
    }


def append_summary(row: dict[str, Any]) -> None:
    SUMMARY_CSV.parent.mkdir(parents=True, exist_ok=True)
    exists = SUMMARY_CSV.exists()
    fields = [
        "candidate",
        "mode",
        "tf",
        "total_return",
        "sharpe",
        "max_drawdown",
        "final_equity",
        "fills",
        "intents",
        "periods",
        "run_dir",
        "params",
    ]
    with SUMMARY_CSV.open("a", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({k: row.get(k) for k in fields})


def reset_output_dir() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in (SUMMARY_CSV, SUMMARY_JSON):
        if old.exists():
            old.unlink()
    for child in OUT_DIR.glob("beda_modes_*"):
        if child.is_dir():
            shutil.rmtree(child)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-trials", type=int, default=30)
    args = parser.parse_args()
    reset_output_dir()

    rows: list[dict[str, Any]] = []
    items = candidates(max(1, args.max_trials))
    for item in items:
        tfs = ["1m"] if item["mode"] == "mtf_bb_reentry_beda_filter" else ["1m", "5m"]
        for tf in tfs:
            row = run_one(tf, item)
            rows.append(row)
            append_summary(row)
            print(
                f"{row['candidate']:02d} {row['mode']} {row['tf']} "
                f"ret={row['total_return']:.2%} sharpe={row['sharpe']:.2f} "
                f"dd={row['max_drawdown']:.2%} fills={row['fills']}"
            )

    winners = [
        row
        for row in rows
        if row["total_return"] > 0 and row["sharpe"] > 0 and row["fills"] > 0
    ]
    best = sorted(rows, key=lambda r: (r["total_return"], r["sharpe"]), reverse=True)[:10]
    SUMMARY_JSON.write_text(
        json.dumps(
            {
                "max_trials": args.max_trials,
                "evaluated_candidates": len(items),
                "evaluated_runs": len(rows),
                "winners": winners,
                "best": best,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"summary={SUMMARY_CSV}")
    print(f"winners={len(winners)}")
    return 0 if winners else 1


if __name__ == "__main__":
    raise SystemExit(main())
