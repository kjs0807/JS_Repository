"""Stateful indicators (PR 16 — FRAMA Channel).

Phase 1 stateless indicators (BB, ATR, KC, RSI) compose well with polars
expressions, but recursive smoothers like FRAMA need an explicit per-bar loop —
hence the separate ``stateful`` namespace. Indicators here still satisfy the
``Indicator`` protocol (``required_warmup_bars`` + ``compute(bars)``) so the
``IndicatorEngine`` can precompute them with no special handling.
"""

from backtester.indicators.stateful.beda import BedaBand
from backtester.indicators.stateful.frama import FRAMAChannel
from backtester.indicators.stateful.sats import SATSConfig, SATSIndicator

__all__ = ["BedaBand", "FRAMAChannel", "SATSConfig", "SATSIndicator"]
