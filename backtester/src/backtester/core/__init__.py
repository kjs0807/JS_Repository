"""Core types + config + factory (Phase 2.5+)."""

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.config_factory import crypto_perp_backtest_config
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
    BracketSpec,
    ClosePosition,
    FullEquityNotional,
    FullPosition,
    OrderAction,
    OrderIntent,
    ScaleIn,
    SizeSpec,
    TargetMarginPct,
    TargetNotional,
    TargetNotionalPct,
    TargetUnits,
    TargetWeight,
)
from backtester.core.preset_loader import load_preset_yaml
from backtester.core.result import BacktestResult
from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import BarPathModel, Fill

__all__ = [
    # config
    "BacktestConfig",
    "DataSourceConfig",
    "crypto_perp_backtest_config",
    "load_preset_yaml",
    # errors
    "BacktestError",
    "ConfigError",
    "DataError",
    "ExecutionError",
    "InstrumentError",
    "RiskError",
    "RunDirectoryError",
    # orders
    "BracketSpec",
    "ClosePosition",
    "FullEquityNotional",
    "FullPosition",
    "OrderAction",
    "OrderIntent",
    "ScaleIn",
    "SizeSpec",
    "TargetMarginPct",
    "TargetNotional",
    "TargetNotionalPct",
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
