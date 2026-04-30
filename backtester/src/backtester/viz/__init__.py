"""Visualization layer (Phase 1.5+).

Phase 1.5 PR 10: equity 시리즈 (drawdown 포함).
Phase 1.5 PR 11: run_chart (Plotly).
Phase 2: metrics, report.
"""

from backtester.viz.equity import build_equity_series

__all__ = ["build_equity_series"]
