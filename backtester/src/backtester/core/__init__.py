"""Core types and interfaces (PR 1)."""

from backtester.core.errors import (
    BacktestError,
    ConfigError,
    DataError,
    ExecutionError,
    InstrumentError,
    RiskError,
    RunDirectoryError,
)
from backtester.core.orders import (
    ClosePosition,
    FullPosition,
    OrderIntent,
    ScaleIn,
    SizeSpec,
    TargetNotional,
    TargetUnits,
    TargetWeight,
)
from backtester.core.result import BacktestResult
from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import BarPathModel, Fill

__all__ = [
    # errors
    "BacktestError",
    "ConfigError",
    "DataError",
    "ExecutionError",
    "InstrumentError",
    "RiskError",
    "RunDirectoryError",
    # orders
    "ClosePosition",
    "FullPosition",
    "OrderIntent",
    "ScaleIn",
    "SizeSpec",
    "TargetNotional",
    "TargetUnits",
    "TargetWeight",
    # snapshot
    "MarketSnapshot",
    # result
    "BacktestResult",
    # types
    "BarPathModel",
    "Fill",
]
