"""Visualization layer (Phase 1.5+).

Phase 1.5 PR 10: equity 시리즈 (drawdown 포함).
Phase 1.5 PR 11: run_chart (Plotly 4단 subplot).
Phase 2 PR 18: metrics + HTML report.
"""

from backtester.viz.equity import build_equity_series
from backtester.viz.metrics import compute_core_metrics, daily_resample
from backtester.viz.report import render_metrics_report
from backtester.viz.run_chart import build_run_chart, render_run_chart

__all__ = [
    "build_equity_series",
    "build_run_chart",
    "compute_core_metrics",
    "daily_resample",
    "render_metrics_report",
    "render_run_chart",
]
