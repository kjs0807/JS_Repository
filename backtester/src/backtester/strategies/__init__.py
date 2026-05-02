"""Strategy base + 구현체 + registry (Phase 1.5)."""

from backtester.strategies.base import BaseStrategy
from backtester.strategies.bbkc_legacy_compat import BBKCLegacyCompatStrategy
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy
from backtester.strategies.registry import STRATEGY_REGISTRY, build_strategy

__all__ = [
    "STRATEGY_REGISTRY",
    "BBKCLegacyCompatStrategy",
    "BBKCSqueezeStrategy",
    "BaseStrategy",
    "build_strategy",
]
