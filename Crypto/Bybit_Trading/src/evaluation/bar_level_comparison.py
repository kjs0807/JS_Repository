"""Bar-level filter verdict for filter-type ML patterns.

Implements Option C from
``docs/superpowers/specs/ml/2026-04-14_d2_bar_level_baseline_verdict_design.md``.

Purpose
-------
``src/ml/validator.py::evaluate_holdout`` already emits an event-level
``filter_verdict`` that compares ML-accepted events to the threshold=0
baseline on R-multiples. That is necessary but not sufficient for
filter-type patterns (BBKC filter wrapping BBKCSqueeze). A filter can
look like ``FILTER_VALUE_ADD`` at event level and still destroy the raw
strategy's deployment P&L — that is exactly what happened with BBKC
filter Day 2 (93% P&L loss vs raw).

This module answers the real deployment question: when both the raw
baseline strategy and the ML wrapper are run through the actual
``BacktestEngine`` (with position lock, intra-bar TP/SL, broker sizing)
on the same holdout window, does the ML wrapper beat the raw baseline
per-trade?

Scope
-----
Filter-type patterns only. Standalone patterns (RSI divergence,
EngulfingMTF) have no baseline strategy to compare against — the
comparator returns ``BAR_FILTER_NOT_COMPARABLE`` and the caller should
rely on the D1 event-level verdict alone.

Output verdict values
---------------------
``BAR_FILTER_VALUE_ADD``     -- ml per-trade P&L and win_rate both improved
``BAR_FILTER_DESTROYS``      -- both worsened
``BAR_FILTER_NEUTRAL``       -- one axis up, one down (ambiguous)
``BAR_FILTER_NOT_COMPARABLE`` -- either arm has < min_trades_for_judgement
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.backtester.engine import BacktestEngine
from src.core.config import RiskConfig
from src.data_manager.db import DBManager
from src.evaluation.holdout import (
    HoldoutSpec,
    run_strategy_on_holdout,
)


@dataclass
class BarLevelMetrics:
    n_trades: int
    total_pnl: float
    win_rate: float
    avg_trade_pnl: float
    sharpe: float
    max_drawdown: float
    per_symbol: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_holdout_result(cls, run: Dict[str, Any]) -> "BarLevelMetrics":
        agg = run["aggregate"]
        return cls(
            n_trades=int(agg["n_trades"]),
            total_pnl=float(agg["total_pnl"]),
            win_rate=float(agg["win_rate"]),
            avg_trade_pnl=float(agg["avg_trade_pnl"]),
            sharpe=float(agg["sharpe"]),
            max_drawdown=float(agg["max_drawdown"]),
            per_symbol=run["per_symbol"],
        )


@dataclass
class BarLevelComparison:
    holdout_period_ms: Tuple[int, int]
    symbols: List[str]
    baseline_strategy_name: str
    ml_wrapper_name: str
    raw: BarLevelMetrics
    ml: BarLevelMetrics
    delta_trade_count: int
    delta_win_rate: float
    delta_total_pnl: float
    delta_avg_trade_pnl: float
    delta_sharpe: float
    delta_max_drawdown: float
    bar_level_filter_verdict: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "holdout_period_ms": list(self.holdout_period_ms),
            "symbols": self.symbols,
            "baseline_strategy_name": self.baseline_strategy_name,
            "ml_wrapper_name": self.ml_wrapper_name,
            "raw": asdict(self.raw),
            "ml": asdict(self.ml),
            "delta": {
                "trade_count": self.delta_trade_count,
                "win_rate": self.delta_win_rate,
                "total_pnl": self.delta_total_pnl,
                "avg_trade_pnl": self.delta_avg_trade_pnl,
                "sharpe": self.delta_sharpe,
                "max_drawdown": self.delta_max_drawdown,
            },
            "bar_level_filter_verdict": self.bar_level_filter_verdict,
        }


def _derive_bar_filter_verdict(
    raw: BarLevelMetrics,
    ml: BarLevelMetrics,
    min_trades_for_judgement: int = 5,
    eps: float = 1e-9,
) -> str:
    """Classify the comparison.

    Judgment is **per-trade** (avg_trade_pnl and win_rate), not on
    ``total_pnl``. A legitimate filter is allowed to reduce trade count —
    that is the point. Judging by total P&L would punish every filter
    that rejects anything at all.
    """
    if raw.n_trades < min_trades_for_judgement:
        return "BAR_FILTER_NOT_COMPARABLE"
    if ml.n_trades < min_trades_for_judgement:
        return "BAR_FILTER_NOT_COMPARABLE"

    wr_up = (ml.win_rate - raw.win_rate) > eps
    wr_down = (ml.win_rate - raw.win_rate) < -eps
    avg_up = (ml.avg_trade_pnl - raw.avg_trade_pnl) > eps
    avg_down = (ml.avg_trade_pnl - raw.avg_trade_pnl) < -eps

    if wr_up and avg_up:
        return "BAR_FILTER_VALUE_ADD"
    if wr_down and avg_down:
        return "BAR_FILTER_DESTROYS"
    return "BAR_FILTER_NEUTRAL"


def compare_ml_vs_baseline(
    baseline_strategy_name: str,
    baseline_factory: Callable[[], Any],
    ml_wrapper_name: str,
    ml_factory: Callable[[], Any],
    spec: HoldoutSpec,
    db: DBManager,
    engine: Optional[BacktestEngine] = None,
    risk_config: Optional[RiskConfig] = None,
    min_trades_for_judgement: int = 5,
) -> BarLevelComparison:
    """Run both arms through ``BacktestEngine`` and compute deltas.

    Both arms use the exact same ``HoldoutSpec`` so the comparison is
    apples-to-apples. The ``run_strategy_on_holdout`` contract means the
    feed warmup, holdout filter, and metric computation are identical
    to the rule-based verdict flow — no secondary reference frame.
    """
    if engine is None:
        engine = BacktestEngine()
    if risk_config is None:
        risk_config = RiskConfig()

    raw_run = run_strategy_on_holdout(
        baseline_factory, spec, db, engine, risk_config,
    )
    ml_run = run_strategy_on_holdout(
        ml_factory, spec, db, engine, risk_config,
    )

    raw_metrics = BarLevelMetrics.from_holdout_result(raw_run)
    ml_metrics = BarLevelMetrics.from_holdout_result(ml_run)

    verdict = _derive_bar_filter_verdict(
        raw_metrics, ml_metrics, min_trades_for_judgement,
    )

    return BarLevelComparison(
        holdout_period_ms=(spec.holdout_start_ms, spec.holdout_end_ms),
        symbols=list(spec.symbols),
        baseline_strategy_name=baseline_strategy_name,
        ml_wrapper_name=ml_wrapper_name,
        raw=raw_metrics,
        ml=ml_metrics,
        delta_trade_count=ml_metrics.n_trades - raw_metrics.n_trades,
        delta_win_rate=ml_metrics.win_rate - raw_metrics.win_rate,
        delta_total_pnl=ml_metrics.total_pnl - raw_metrics.total_pnl,
        delta_avg_trade_pnl=(
            ml_metrics.avg_trade_pnl - raw_metrics.avg_trade_pnl
        ),
        delta_sharpe=ml_metrics.sharpe - raw_metrics.sharpe,
        delta_max_drawdown=(
            ml_metrics.max_drawdown - raw_metrics.max_drawdown
        ),
        bar_level_filter_verdict=verdict,
    )


__all__ = [
    "BarLevelMetrics",
    "BarLevelComparison",
    "compare_ml_vs_baseline",
]
