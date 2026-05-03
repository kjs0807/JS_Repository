"""Indicators (PR 3, PR 8, PR 16).

Stateless: BB, ATR, KC, RSI (compose with polars expressions).
Stateful: FRAMAChannel (PR 16, recursive Pine port).
"""

from backtester.indicators.base import Indicator
from backtester.indicators.engine import IndicatorEngine
from backtester.indicators.stateful.frama import FRAMAChannel
from backtester.indicators.stateless.atr import ATR
from backtester.indicators.stateless.bb import BollingerBands
from backtester.indicators.stateless.ema import EMA
from backtester.indicators.stateless.kc import KeltnerChannel

__all__ = [
    "ATR",
    "BollingerBands",
    "EMA",
    "FRAMAChannel",
    "Indicator",
    "IndicatorEngine",
    "KeltnerChannel",
]
