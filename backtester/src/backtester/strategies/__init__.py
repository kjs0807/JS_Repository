"""Strategy base + 구현체."""

from backtester.strategies.base import BaseStrategy
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy

__all__ = ["BBKCSqueezeStrategy", "BaseStrategy"]
