"""Tests for ``src/evaluation/bar_level_comparison.py``.

We do not spin up the real BacktestEngine here — the purpose is to
lock in ``_derive_bar_filter_verdict`` and ``compare_ml_vs_baseline``
wiring. Engine-level runs are covered by the experiment scripts
themselves (the scripts ARE their own smoke tests).
"""
from __future__ import annotations

import json

from src.evaluation.bar_level_comparison import (
    BarLevelComparison,
    BarLevelMetrics,
    _derive_bar_filter_verdict,
)


def _mk(
    n: int = 50,
    total: float = 0.0,
    wr: float = 0.5,
    avg: float = 0.0,
    sharpe: float = 0.0,
    mdd: float = 0.1,
) -> BarLevelMetrics:
    return BarLevelMetrics(
        n_trades=n,
        total_pnl=total,
        win_rate=wr,
        avg_trade_pnl=avg,
        sharpe=sharpe,
        max_drawdown=mdd,
    )


class TestBarLevelVerdict:
    def test_value_add_when_both_metrics_improve(self) -> None:
        raw = _mk(n=100, wr=0.50, avg=1.0)
        ml = _mk(n=30,  wr=0.60, avg=1.5)
        assert _derive_bar_filter_verdict(raw, ml) == "BAR_FILTER_VALUE_ADD"

    def test_destroys_when_both_worsen(self) -> None:
        raw = _mk(n=100, wr=0.60, avg=2.0)
        ml = _mk(n=30,  wr=0.55, avg=1.0)
        assert _derive_bar_filter_verdict(raw, ml) == "BAR_FILTER_DESTROYS"

    def test_neutral_when_mixed(self) -> None:
        raw = _mk(n=100, wr=0.60, avg=2.0)
        # wr up, avg down
        ml = _mk(n=30,  wr=0.70, avg=1.5)
        assert _derive_bar_filter_verdict(raw, ml) == "BAR_FILTER_NEUTRAL"

    def test_not_comparable_when_raw_empty(self) -> None:
        raw = _mk(n=2, wr=0.5, avg=1.0)
        ml = _mk(n=50, wr=0.6, avg=1.5)
        assert _derive_bar_filter_verdict(raw, ml) == "BAR_FILTER_NOT_COMPARABLE"

    def test_not_comparable_when_ml_empty(self) -> None:
        raw = _mk(n=50, wr=0.5, avg=1.0)
        ml = _mk(n=2, wr=1.0, avg=10.0)
        assert _derive_bar_filter_verdict(raw, ml) == "BAR_FILTER_NOT_COMPARABLE"


class TestBarLevelComparisonDataclass:
    def test_to_dict_json_roundtrip(self) -> None:
        cmp = BarLevelComparison(
            holdout_period_ms=(1700000000000, 1710000000000),
            symbols=["BTCUSDT"],
            baseline_strategy_name="BBKCSqueeze",
            ml_wrapper_name="PatternMLFilterStrategy[bbkc_filter]",
            raw=_mk(n=100, wr=0.60, avg=2.0, total=200.0, sharpe=1.0, mdd=0.15),
            ml=_mk(n=30, wr=0.55, avg=1.0, total=30.0, sharpe=0.3, mdd=0.08),
            delta_trade_count=-70,
            delta_win_rate=-0.05,
            delta_total_pnl=-170.0,
            delta_avg_trade_pnl=-1.0,
            delta_sharpe=-0.7,
            delta_max_drawdown=-0.07,
            bar_level_filter_verdict="BAR_FILTER_DESTROYS",
        )
        d = cmp.to_dict()
        s = json.dumps(d)
        d2 = json.loads(s)
        assert d2["bar_level_filter_verdict"] == "BAR_FILTER_DESTROYS"
        assert d2["raw"]["n_trades"] == 100
        assert d2["ml"]["n_trades"] == 30
        assert d2["delta"]["avg_trade_pnl"] == -1.0
