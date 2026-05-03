"""Strategy base + 구현체 + registry (Phase 1.5, PR 16 FRAMA)."""

from backtester.strategies.base import BaseStrategy
from backtester.strategies.bbkc_legacy_compat import BBKCLegacyCompatStrategy
from backtester.strategies.bbkc_multi_legacy_compat import BBKCMultiLegacyCompatStrategy
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy
from backtester.strategies.frama_channel import FRAMAChannelStrategy
from backtester.strategies.frama_ema200_channel import (
    FRAMAEMA200ChannelStrategy,
    FRAMAMultiEMA200ChannelStrategy,
)
from backtester.strategies.frama_multi_channel import FRAMAMultiChannelStrategy
from backtester.strategies.registry import STRATEGY_REGISTRY, build_strategy

__all__ = [
    "STRATEGY_REGISTRY",
    "BBKCLegacyCompatStrategy",
    "BBKCMultiLegacyCompatStrategy",
    "BBKCSqueezeStrategy",
    "BaseStrategy",
    "FRAMAChannelStrategy",
    "FRAMAEMA200ChannelStrategy",
    "FRAMAMultiEMA200ChannelStrategy",
    "FRAMAMultiChannelStrategy",
    "build_strategy",
]
