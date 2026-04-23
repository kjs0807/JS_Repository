"""Tests for ``src/evaluation/holdout.py::compute_metrics_from_trades``.

The engine is NOT exercised here — we feed synthetic TradeRecord-like
objects and check that the metric block matches
``logs/variant_round1/results.json`` format expected by the verdict
logic. An engine-level smoke test lives in
``test_bar_level_comparison.py`` so these two layers are separable.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.evaluation.holdout import compute_metrics_from_trades


@dataclass
class _Trade:
    pnl: float
    entry_time: int = 0


class TestComputeMetrics:
    def test_empty_trades_returns_zeroed_block(self) -> None:
        m = compute_metrics_from_trades([], initial_capital=10_000.0)
        assert m["n_trades"] == 0
        assert m["n_wins"] == 0
        assert m["n_losses"] == 0
        assert m["total_pnl"] == 0.0
        assert m["win_rate"] == 0.0
        assert m["avg_trade_pnl"] == 0.0
        assert m["sharpe"] == 0.0
        assert m["max_drawdown"] == 0.0

    def test_all_wins(self) -> None:
        trades = [_Trade(10.0), _Trade(20.0), _Trade(30.0)]
        m = compute_metrics_from_trades(trades, initial_capital=1000.0)
        assert m["n_trades"] == 3
        assert m["n_wins"] == 3
        assert m["n_losses"] == 0
        assert m["total_pnl"] == pytest.approx(60.0)
        assert m["win_rate"] == 1.0
        assert m["max_drawdown"] == 0.0

    def test_all_losses_drawdown(self) -> None:
        trades = [_Trade(-100.0), _Trade(-100.0), _Trade(-100.0)]
        m = compute_metrics_from_trades(trades, initial_capital=1000.0)
        assert m["n_trades"] == 3
        assert m["n_losses"] == 3
        assert m["total_pnl"] == pytest.approx(-300.0)
        assert m["win_rate"] == 0.0
        # Drawdown uses the post-cumsum equity array only (no initial
        # capital as a pre-trade data point). equity = [900, 800, 700],
        # running peak = [900, 900, 900], max dd = 200/900.
        # This matches the behavior expected by round 1 results.json.
        assert m["max_drawdown"] == pytest.approx(200.0 / 900.0, abs=1e-6)

    def test_mixed_pnl_partial_drawdown(self) -> None:
        # +100 -200 +50 equity: 1100, 900, 950. peak=1100. max dd = 200/1100 ~= 0.1818
        trades = [_Trade(100.0), _Trade(-200.0), _Trade(50.0)]
        m = compute_metrics_from_trades(trades, initial_capital=1000.0)
        assert m["total_pnl"] == pytest.approx(-50.0)
        assert m["win_rate"] == pytest.approx(2 / 3)
        assert m["max_drawdown"] == pytest.approx(200.0 / 1100.0, abs=1e-6)
