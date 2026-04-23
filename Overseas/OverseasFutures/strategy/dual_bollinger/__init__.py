"""Dual Bollinger Band Breakout Strategy — core package."""

from strategy.dual_bollinger.config import DualBBConfig
from strategy.dual_bollinger.events import EventType, State, StrategyEvent, Position, TradeRecord
from strategy.dual_bollinger.bands import calculate_bands
from strategy.dual_bollinger.state_machine import DualBBStateMachine
from strategy.dual_bollinger.engine import DualBBBacktestEngine, DualBBBacktestResult
from strategy.dual_bollinger.report import print_report, plot_equity_curve
from strategy.dual_bollinger.optimizer import (
    ParameterGrid, WalkForwardValidator, WalkForwardResult,
    GridSearchOptimizer, print_optimization_report, plot_walk_forward,
)

__all__ = [
    "DualBBConfig",
    "EventType", "State", "StrategyEvent", "Position", "TradeRecord",
    "calculate_bands",
    "DualBBStateMachine",
    "DualBBBacktestEngine", "DualBBBacktestResult",
    "print_report", "plot_equity_curve",
    "ParameterGrid", "WalkForwardValidator", "WalkForwardResult",
    "GridSearchOptimizer", "print_optimization_report", "plot_walk_forward",
]
