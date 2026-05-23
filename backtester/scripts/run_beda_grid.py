"""Run a bounded Beda+Bollinger parameter search.

The script evaluates up to 30 condition sets.  Each set is first run on 5m;
sets with positive 5m return and Sharpe are then confirmed on 1m.  This keeps
the expensive 1m backtests focused while still using the real BacktestEngine
for every reported result.
"""

from __future__ import annotations

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
OUT_DIR = ROOT / "runs" / "beda_grid"
SUMMARY_CSV = OUT_DIR / "grid_summary.csv"
SUMMARY_JSON = OUT_DIR / "grid_summary.json"
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 5, 7, tzinfo=timezone.utc)
INITIAL_EQUITY = Decimal("50000")


def candidates() -> list[dict[str, Any]]:
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
    grid: list[dict[str, Any]] = []
    specs = [
        # Early reversal, tight TP, strict stop.
        dict(long_signal_rsi_max=55, short_signal_rsi_min=45, take_profit_pct="0.003", max_stop_distance_pct="0.006", time_stop_bars=18),
        dict(long_signal_rsi_max=52, short_signal_rsi_min=48, take_profit_pct="0.003", max_stop_distance_pct="0.005", time_stop_bars=12),
        dict(long_signal_rsi_max=58, short_signal_rsi_min=42, take_profit_pct="0.004", max_stop_distance_pct="0.008", time_stop_bars=24),
        dict(long_signal_rsi_max=60, short_signal_rsi_min=40, take_profit_pct="0.005", max_stop_distance_pct="0.010", time_stop_bars=36),
        # Require signal candle still on the wrong side of mid.
        dict(long_signal_rsi_max=55, short_signal_rsi_min=45, require_signal_close_below_mid_long=True, require_signal_close_above_mid_short=True, take_profit_pct="0.004", max_stop_distance_pct="0.008", time_stop_bars=24),
        dict(long_signal_rsi_max=58, short_signal_rsi_min=42, require_signal_close_below_mid_long=True, require_signal_close_above_mid_short=True, take_profit_pct="0.006", max_stop_distance_pct="0.012", time_stop_bars=48),
        # Require confirmation candle to move in the trade direction.
        dict(long_signal_rsi_max=55, short_signal_rsi_min=45, require_entry_close_above_signal_close_long=True, require_entry_close_below_signal_close_short=True, take_profit_pct="0.004", max_stop_distance_pct="0.008", time_stop_bars=24),
        dict(long_signal_rsi_max=60, short_signal_rsi_min=40, require_entry_close_above_signal_close_long=True, require_entry_close_below_signal_close_short=True, take_profit_pct="0.006", max_stop_distance_pct="0.012", time_stop_bars=36),
        # Narrow stop window avoids tiny chop and very wide risk.
        dict(long_signal_rsi_max=55, short_signal_rsi_min=45, min_stop_distance_pct="0.001", max_stop_distance_pct="0.006", take_profit_pct="0.003", time_stop_bars=18),
        dict(long_signal_rsi_max=58, short_signal_rsi_min=42, min_stop_distance_pct="0.0015", max_stop_distance_pct="0.010", take_profit_pct="0.005", time_stop_bars=30),
        # RSI exits only, no fixed TP.
        dict(long_signal_rsi_max=55, short_signal_rsi_min=45, long_rsi_take_profit=60, short_rsi_take_profit=40, max_stop_distance_pct="0.008", time_stop_bars=24),
        dict(long_signal_rsi_max=52, short_signal_rsi_min=48, long_rsi_take_profit=58, short_rsi_take_profit=42, max_stop_distance_pct="0.006", time_stop_bars=18),
        # Long-only / short-only diagnostics.
        dict(allow_short=False, long_signal_rsi_max=55, take_profit_pct="0.004", max_stop_distance_pct="0.008", time_stop_bars=24),
        dict(allow_long=False, short_signal_rsi_min=45, take_profit_pct="0.004", max_stop_distance_pct="0.008", time_stop_bars=24),
        dict(allow_short=False, require_signal_close_below_mid_long=True, require_entry_close_above_signal_close_long=True, long_signal_rsi_max=58, take_profit_pct="0.006", max_stop_distance_pct="0.012", time_stop_bars=48),
        dict(allow_long=False, require_signal_close_above_mid_short=True, require_entry_close_below_signal_close_short=True, short_signal_rsi_min=42, take_profit_pct="0.006", max_stop_distance_pct="0.012", time_stop_bars=48),
        # Faster/slower Beda variants.
        dict(atr_period=10, slow_mult=1.7, fast_mult=0.8, long_signal_rsi_max=55, short_signal_rsi_min=45, take_profit_pct="0.004", max_stop_distance_pct="0.008", time_stop_bars=24),
        dict(atr_period=18, slow_mult=2.3, fast_mult=1.2, long_signal_rsi_max=58, short_signal_rsi_min=42, take_profit_pct="0.006", max_stop_distance_pct="0.012", time_stop_bars=48),
        # Bollinger sensitivity variants.
        dict(bb_period=30, bb_std=2.0, long_signal_rsi_max=55, short_signal_rsi_min=45, take_profit_pct="0.004", max_stop_distance_pct="0.008", time_stop_bars=24),
        dict(bb_period=20, bb_std=2.5, long_signal_rsi_max=58, short_signal_rsi_min=42, take_profit_pct="0.006", max_stop_distance_pct="0.012", time_stop_bars=36),
        dict(bb_period=10, bb_std=2.0, long_signal_rsi_max=52, short_signal_rsi_min=48, take_profit_pct="0.003", max_stop_distance_pct="0.006", time_stop_bars=12),
        # Opposite start exit disabled with fixed TP/time stop.
        dict(exit_on_opposite_start=False, long_signal_rsi_max=55, short_signal_rsi_min=45, take_profit_pct="0.003", max_stop_distance_pct="0.006", time_stop_bars=12),
        dict(exit_on_opposite_start=False, long_signal_rsi_max=58, short_signal_rsi_min=42, take_profit_pct="0.005", max_stop_distance_pct="0.010", time_stop_bars=24),
        # Entry-bar RSI guards.
        dict(long_signal_rsi_max=58, short_signal_rsi_min=42, long_entry_rsi_max=60, short_entry_rsi_min=40, take_profit_pct="0.004", max_stop_distance_pct="0.008", time_stop_bars=24),
        dict(long_signal_rsi_max=55, short_signal_rsi_min=45, long_entry_rsi_max=58, short_entry_rsi_min=42, take_profit_pct="0.003", max_stop_distance_pct="0.006", time_stop_bars=18),
        # A few wider-trend attempts.
        dict(long_signal_rsi_max=65, short_signal_rsi_min=35, take_profit_pct="0.008", max_stop_distance_pct="0.016", time_stop_bars=72),
        dict(long_signal_rsi_max=62, short_signal_rsi_min=38, require_entry_close_above_signal_close_long=True, require_entry_close_below_signal_close_short=True, take_profit_pct="0.008", max_stop_distance_pct="0.016", time_stop_bars=72),
        dict(long_rsi_take_profit=70, short_rsi_take_profit=30, long_signal_rsi_max=60, short_signal_rsi_min=40, max_stop_distance_pct="0.012", time_stop_bars=60),
        dict(long_signal_rsi_max=50, short_signal_rsi_min=50, take_profit_pct="0.003", max_stop_distance_pct="0.005", time_stop_bars=12),
        dict(allow_short=False, bb_period=30, long_signal_rsi_max=55, take_profit_pct="0.005", max_stop_distance_pct="0.010", time_stop_bars=36),
    ]
    for i, spec in enumerate(specs[:30], start=1):
        params = {**common, **spec}
        params.setdefault("long_rsi_take_profit", 65.0)
        params.setdefault("short_rsi_take_profit", 35.0)
        grid.append({"candidate": i, "params": params})
    return grid


def sharpe_from_equity(path: Path, tf: str) -> dict[str, float | int]:
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


def run_one(tf: str, candidate: int, params: dict[str, Any]) -> dict[str, Any]:
    base = BacktestConfig.from_yaml(CONFIGS[tf])
    run_id = f"beda_grid_{candidate:02d}_{tf}"
    cfg = replace(
        base,
        run_id=run_id,
        start=START,
        end=END,
        output_dir=OUT_DIR,
        persist_run_data="none",
        snapshot_every_bars=60 if tf == "1m" else 12,
        on_run_exists="overwrite",
        strategy_params=params,
    )
    strategy = build_strategy(cfg.strategy_name, cfg.strategy_params)
    result = BacktestEngine(cfg, strategy, verbose=False).run()
    metrics = sharpe_from_equity(result.run_dir, tf)
    return {
        "candidate": candidate,
        "tf": tf,
        "run_dir": str(result.run_dir),
        **metrics,
        "params": json.dumps(params, sort_keys=True),
    }


def append_summary(row: dict[str, Any]) -> None:
    SUMMARY_CSV.parent.mkdir(parents=True, exist_ok=True)
    exists = SUMMARY_CSV.exists()
    fields = [
        "candidate",
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


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in (SUMMARY_CSV, SUMMARY_JSON):
        if old.exists():
            old.unlink()
    # Keep old non-grid artifacts out of this search directory.
    for child in OUT_DIR.glob("beda_grid_*"):
        if child.is_dir():
            shutil.rmtree(child)

    rows: list[dict[str, Any]] = []
    winner: int | None = None
    for item in candidates():
        idx = item["candidate"]
        params = item["params"]
        row_5m = run_one("5m", idx, params)
        rows.append(row_5m)
        append_summary(row_5m)
        print(
            f"{idx:02d} 5m ret={row_5m['total_return']:.2%} "
            f"sharpe={row_5m['sharpe']:.2f} fills={row_5m['fills']}"
        )
        if row_5m["total_return"] <= 0 or row_5m["sharpe"] <= 0:
            continue
        row_1m = run_one("1m", idx, params)
        rows.append(row_1m)
        append_summary(row_1m)
        print(
            f"{idx:02d} 1m ret={row_1m['total_return']:.2%} "
            f"sharpe={row_1m['sharpe']:.2f} fills={row_1m['fills']}"
        )
        if row_1m["total_return"] > 0 and row_1m["sharpe"] > 0:
            winner = idx
            break

    SUMMARY_JSON.write_text(
        json.dumps(
            {
                "winner": winner,
                "rows": rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"summary={SUMMARY_CSV}")
    print(f"winner={winner}")
    return 0 if winner is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
