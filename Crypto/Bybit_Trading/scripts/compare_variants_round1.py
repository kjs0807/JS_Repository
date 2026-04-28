"""Rule-based improvement round 1 comparison driver.

Runs the three baseline strategies and their four variants on the
same holdout window (2025-10-01 to 2026-04-10) across the five
production symbols, then prints per-symbol and aggregate metrics for
each strategy and saves a JSON dump for downstream reporting.

Fairness notes:
  - Feed starts 14 days BEFORE the holdout so every variant has time
    to clear its warmup window even when ``warmup_bars`` is large
    (the HTF variant needs 200+ 1h bars for 4h EMA(50)).
  - Trades are filtered by ``entry_time >= holdout_start`` before
    computing metrics, so warmup-period trades that leak through are
    excluded for every strategy uniformly.
  - Both baseline and variant use default parameters registered in
    ``src/strategies/registry_builder.py``.

Usage:
    python -m scripts.compare_variants_round1
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List

import numpy as np

from src.backtester.engine import BacktestEngine, BacktestResult
from src.core.config import BacktestConfig, RiskConfig, load_config
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed

from src.strategies.bbkc_squeeze import BBKCSqueeze
from src.strategies.bbkc_squeeze_htf_trend import BBKCSqueezeHTFTrend
from src.strategies.donchian_fixed_rr import DonchianFixedRR
from src.strategies.donchian_fixed_rr_trend_filter import (
    DonchianFixedRRTrendFilter,
)
from src.strategies.donchian_trend_filter import DonchianTrendFilter
from src.strategies.donchian_trend_filter_adx import (
    DonchianTrendFilterADX20,
    DonchianTrendFilterADX25,
)


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"]
HOLDOUT_START_DT = datetime(2025, 10, 1)
HOLDOUT_END_DT = datetime(2026, 4, 10)
WARMUP_DAYS = 14
INITIAL_CAPITAL = 10_000.0


STRATEGIES: List[tuple[str, Callable[[], Any]]] = [
    ("DonchianFixedRR",            lambda: DonchianFixedRR()),
    ("DonchianFixedRRTrendFilter", lambda: DonchianFixedRRTrendFilter()),
    ("DonchianTrendFilter",        lambda: DonchianTrendFilter()),
    ("DonchianTrendFilterADX20",   lambda: DonchianTrendFilterADX20()),
    ("DonchianTrendFilterADX25",   lambda: DonchianTrendFilterADX25()),
    ("BBKCSqueeze",                lambda: BBKCSqueeze()),
    ("BBKCSqueezeHTFTrend",        lambda: BBKCSqueezeHTFTrend()),
]


def _compute_metrics_from_trades(
    trades: list, initial_capital: float,
) -> Dict[str, Any]:
    """Metrics from a filtered trade list. Max drawdown is the
    cumulative-pnl drawdown treating each trade's pnl as a realized
    step from the starting equity."""
    if not trades:
        return {
            "n_trades": 0,
            "n_wins": 0,
            "n_losses": 0,
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "avg_trade_pnl": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
        }
    pnls = np.array([t.pnl for t in trades], dtype=float)
    wins = int((pnls > 0).sum())
    losses = int(len(pnls) - wins)
    total = float(pnls.sum())
    wr = wins / len(pnls)
    avg = total / len(pnls)
    sharpe = 0.0
    if len(pnls) > 1:
        std = float(np.std(pnls, ddof=1))
        if std > 0:
            sharpe = (float(np.mean(pnls)) / std) * np.sqrt(252.0)
    equity = initial_capital + np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    dd_abs = peak - equity
    dd_pct = dd_abs / np.where(peak > 0, peak, 1.0)
    max_dd = float(dd_pct.max()) if len(dd_pct) else 0.0
    return {
        "n_trades": int(len(pnls)),
        "n_wins": wins,
        "n_losses": losses,
        "total_pnl": total,
        "win_rate": wr,
        "avg_trade_pnl": avg,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
    }


def _run_strategy(
    name: str,
    factory: Callable[[], Any],
    db: DBManager,
    engine: BacktestEngine,
    warmup_start_ms: int,
    holdout_start_ms: int,
    holdout_end_ms: int,
) -> Dict[str, Any]:
    print(f"\n=== {name} ===")
    per_sym: Dict[str, Dict[str, Any]] = {}
    all_trades: list = []
    for sym in SYMBOLS:
        feed = HistoricalDataFeed(
            db=db, symbols=[sym], timeframe="1h",
            start_time=warmup_start_ms, end_time=holdout_end_ms,
        )
        strat = factory()
        result: BacktestResult = engine.run(
            strategy=strat, data_feed=feed,
            config=BacktestConfig(initial_capital=INITIAL_CAPITAL),
            symbol=sym, risk_config=RiskConfig(),
        )
        holdout_trades = [
            t for t in result.trades if t.entry_time >= holdout_start_ms
        ]
        m = _compute_metrics_from_trades(holdout_trades, INITIAL_CAPITAL)
        per_sym[sym] = m
        all_trades.extend(holdout_trades)
        print(
            f"  {sym:10s} | n={m['n_trades']:4d} pnl={m['total_pnl']:+10.2f} "
            f"wr={m['win_rate']:5.1%} avg={m['avg_trade_pnl']:+8.2f} "
            f"sharpe={m['sharpe']:+6.2f} mdd={m['max_drawdown']:5.1%}"
        )
    agg = _compute_metrics_from_trades(all_trades, INITIAL_CAPITAL)
    print(
        f"  {'TOTAL':10s} | n={agg['n_trades']:4d} pnl={agg['total_pnl']:+10.2f} "
        f"wr={agg['win_rate']:5.1%} avg={agg['avg_trade_pnl']:+8.2f} "
        f"sharpe={agg['sharpe']:+6.2f} mdd={agg['max_drawdown']:5.1%}"
    )
    return {"per_symbol": per_sym, "aggregate": agg}


def main() -> int:
    cfg = load_config()
    db = DBManager(cfg.app.db_path)
    engine = BacktestEngine()

    warmup_start_dt = HOLDOUT_START_DT - timedelta(days=WARMUP_DAYS)
    warmup_start_ms = int(warmup_start_dt.timestamp() * 1000)
    holdout_start_ms = int(HOLDOUT_START_DT.timestamp() * 1000)
    holdout_end_ms = int(HOLDOUT_END_DT.timestamp() * 1000)

    print(
        f"Holdout: {HOLDOUT_START_DT.date()} -> {HOLDOUT_END_DT.date()}\n"
        f"Warmup start: {warmup_start_dt.date()} (feed begins here)\n"
        f"Symbols: {', '.join(SYMBOLS)}\n"
        f"Initial capital: {INITIAL_CAPITAL:,.0f}"
    )

    results: Dict[str, Dict[str, Any]] = {}
    for name, factory in STRATEGIES:
        results[name] = _run_strategy(
            name, factory, db, engine,
            warmup_start_ms, holdout_start_ms, holdout_end_ms,
        )

    out_dir = Path("logs/variant_round1")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "results.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
