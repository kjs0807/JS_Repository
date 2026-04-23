"""Holdout-first strategy evaluation primitives.

Every experiment script in this codebase ultimately answers the same
question: "run strategy X on 5 symbols over a fixed holdout window,
filter trades whose entry fell inside the window, then compute
per-symbol + aggregate metrics." This module is the single place that
implements that pipeline so the scripts stay small and comparable.

Key design points:

- The feed starts ``warmup_days`` BEFORE the holdout so every strategy
  (including EMA(200) / 4h-aware variants) has a clean warmup. Trades
  whose ``entry_time`` is still in the warmup window are dropped before
  metrics are computed. This matches the ``compare_variants_round1.py``
  fairness rule.
- Metrics match the format in ``logs/variant_round1/results.json`` so
  downstream verdict scripts can read either path.
- ``run_strategy_on_holdout`` takes a **factory** callable (not an
  instance) because the engine mutates state; callers that compare N
  parameter combinations need fresh instances per run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np

from src.backtester.engine import BacktestEngine, BacktestResult
from src.core.config import BacktestConfig, RiskConfig
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed


@dataclass
class HoldoutSpec:
    """Declarative holdout experiment definition.

    symbols          -- universe to evaluate
    timeframe        -- primary TF (1h for every current strategy)
    holdout_start_dt -- inclusive lower bound for trade.entry_time
    holdout_end_dt   -- exclusive upper bound
    warmup_days      -- how far back the feed starts before holdout_start
    initial_capital  -- BacktestConfig.initial_capital
    """

    symbols: Sequence[str]
    timeframe: str = "1h"
    holdout_start_dt: datetime = field(
        default_factory=lambda: datetime(2025, 10, 1)
    )
    holdout_end_dt: datetime = field(
        default_factory=lambda: datetime(2026, 4, 10)
    )
    warmup_days: int = 14
    initial_capital: float = 10_000.0

    @property
    def holdout_start_ms(self) -> int:
        return int(self.holdout_start_dt.timestamp() * 1000)

    @property
    def holdout_end_ms(self) -> int:
        return int(self.holdout_end_dt.timestamp() * 1000)

    @property
    def warmup_start_ms(self) -> int:
        dt = self.holdout_start_dt - timedelta(days=self.warmup_days)
        return int(dt.timestamp() * 1000)


def compute_metrics_from_trades(
    trades: List[Any],
    initial_capital: float,
) -> Dict[str, Any]:
    """Return the metrics block matching ``results.json`` format.

    Drawdown is the cumulative-pnl drawdown treating each trade's pnl as
    a realized step from initial_capital. This mirrors the rule in
    ``scripts/compare_variants_round1.py`` so existing result files
    remain comparable with new runs.
    """
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


def run_strategy_on_holdout(
    strategy_factory: Callable[[], Any],
    spec: HoldoutSpec,
    db: DBManager,
    engine: Optional[BacktestEngine] = None,
    risk_config: Optional[RiskConfig] = None,
) -> Dict[str, Any]:
    """Run ``strategy_factory()`` on every symbol in ``spec`` sequentially.

    Returns a dict shaped like one entry in ``results.json``::

        {
            "per_symbol": {sym: metrics, ...},
            "aggregate":  metrics,
            "trades":     [TradeRecord, ...],  # holdout-filtered
        }

    ``trades`` is included so downstream consumers (bar-level comparator,
    symbol-subset analysis) don't have to re-run the engine.
    """
    if engine is None:
        engine = BacktestEngine()
    if risk_config is None:
        risk_config = RiskConfig()

    per_sym: Dict[str, Dict[str, Any]] = {}
    all_trades: List[Any] = []

    for sym in spec.symbols:
        feed = HistoricalDataFeed(
            db=db,
            symbols=[sym],
            timeframe=spec.timeframe,
            start_time=spec.warmup_start_ms,
            end_time=spec.holdout_end_ms,
        )
        strat = strategy_factory()
        result: BacktestResult = engine.run(
            strategy=strat,
            data_feed=feed,
            config=BacktestConfig(initial_capital=spec.initial_capital),
            symbol=sym,
            risk_config=risk_config,
        )
        holdout_trades = [
            t for t in result.trades
            if int(t.entry_time) >= spec.holdout_start_ms
            and int(t.entry_time) < spec.holdout_end_ms
        ]
        per_sym[sym] = compute_metrics_from_trades(
            holdout_trades, spec.initial_capital
        )
        all_trades.extend(holdout_trades)

    aggregate = compute_metrics_from_trades(
        all_trades, spec.initial_capital
    )
    return {
        "per_symbol": per_sym,
        "aggregate": aggregate,
        "trades": all_trades,
    }


def run_strategies_on_holdout(
    factories: Iterable[tuple[str, Callable[[], Any]]],
    spec: HoldoutSpec,
    db: DBManager,
    engine: Optional[BacktestEngine] = None,
    risk_config: Optional[RiskConfig] = None,
    print_progress: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """Run several named strategies through ``run_strategy_on_holdout``.

    The output is the same shape as ``logs/variant_round1/results.json``
    minus the transient ``trades`` key (which is not JSON-serializable
    for TradeRecord dataclasses). Callers that need the trades can call
    ``run_strategy_on_holdout`` directly.
    """
    if engine is None:
        engine = BacktestEngine()
    results: Dict[str, Dict[str, Any]] = {}
    for name, factory in factories:
        if print_progress:
            print(f"\n=== {name} ===")
        run = run_strategy_on_holdout(factory, spec, db, engine, risk_config)
        results[name] = {
            "per_symbol": run["per_symbol"],
            "aggregate": run["aggregate"],
        }
        if print_progress:
            _print_summary(name, run["per_symbol"], run["aggregate"])
    return results


def _print_summary(
    name: str,
    per_sym: Dict[str, Dict[str, Any]],
    aggregate: Dict[str, Any],
) -> None:
    for sym, m in per_sym.items():
        print(
            f"  {sym:10s} | n={m['n_trades']:4d} "
            f"pnl={m['total_pnl']:+10.2f} "
            f"wr={m['win_rate']:5.1%} avg={m['avg_trade_pnl']:+8.2f} "
            f"sharpe={m['sharpe']:+6.2f} mdd={m['max_drawdown']:5.1%}"
        )
    m = aggregate
    print(
        f"  {'TOTAL':10s} | n={m['n_trades']:4d} "
        f"pnl={m['total_pnl']:+10.2f} "
        f"wr={m['win_rate']:5.1%} avg={m['avg_trade_pnl']:+8.2f} "
        f"sharpe={m['sharpe']:+6.2f} mdd={m['max_drawdown']:5.1%}"
    )


__all__ = [
    "HoldoutSpec",
    "compute_metrics_from_trades",
    "run_strategy_on_holdout",
    "run_strategies_on_holdout",
]
