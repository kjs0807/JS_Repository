"""Execution models (PR 6).

Phase 1: NextBarOpenExecution만. Slippage / BarPathModel 분기는 Phase 2.
"""

from backtester.execution.base import ExecutionModel
from backtester.execution.next_bar import NextBarOpenExecution

__all__ = ["ExecutionModel", "NextBarOpenExecution"]
