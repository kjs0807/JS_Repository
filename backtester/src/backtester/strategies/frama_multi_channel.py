"""FRAMA Channel multi-symbol wrapper (PR 16).

Mirrors ``BBKCMultiLegacyCompatStrategy`` (PR W) for FRAMA: holds one
``FRAMAChannelStrategy`` child per symbol and dispatches ``on_bar`` to each
with ``ctx.primary_symbol`` swapped via ``dataclasses.replace``.

Design notes:

- The FRAMA indicator instance is owned by the wrapper and shared between
  children, so ``IndicatorEngine.precompute()`` produces exactly one set of
  ``frama_*`` columns per ``(symbol, tf)``. If each child held its own
  indicator, ``horizontal concat`` of duplicate columns would explode.
- All symbols share a single ``timeframe`` (matches BBKC multi assumption).
  A multi-TF multi-symbol variant is left for a future PR.
- ``on_pending_orders`` is forwarded to children with per-symbol-filtered
  pending orders so a child can never see another symbol's stops. Today the
  single-symbol FRAMA strategy doesn't override ``on_pending_orders``, but
  threading the call keeps parity with BBKC and means future trailing logic
  added to the child won't need wrapper changes.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from backtester.core.context import OrderView, StrategyContext
from backtester.core.errors import ConfigError
from backtester.core.orders import OrderAction, OrderIntent
from backtester.indicators.base import Indicator
from backtester.indicators.stateful.frama import FRAMAChannel
from backtester.strategies.base import BaseStrategy
from backtester.strategies.frama_channel import FRAMAChannelStrategy


class FRAMAMultiChannelStrategy(BaseStrategy):
    """Run ``FRAMAChannelStrategy`` across N symbols on a shared timeframe.

    Args:
        symbols: Non-empty list of symbols. Duplicates raise ``ConfigError``
            so a misconfigured YAML cannot silently double-fire entries.
        timeframe: Primary timeframe shared by every symbol.
        child_params: kwargs forwarded to each ``FRAMAChannelStrategy(**...)``.
            Indicator-shaping params (``length``, ``distance``, ``smoothing``,
            ``volatility_window``) are also pulled here to instantiate the
            shared indicator. ``timeframe`` inside ``child_params`` is honoured
            for child construction but the wrapper still overrides
            ``ctx.primary_timeframe`` to ``self.timeframe`` so children see a
            consistent TF regardless of the BacktestConfig primary.
    """

    def __init__(
        self,
        *,
        symbols: list[str],
        timeframe: str = "1h",
        child_params: dict[str, Any] | None = None,
    ) -> None:
        if not symbols:
            raise ConfigError(
                "FRAMAMultiChannelStrategy requires non-empty 'symbols' list"
            )
        if len(set(symbols)) != len(symbols):
            raise ConfigError(
                f"FRAMAMultiChannelStrategy 'symbols' contains duplicates: {symbols}"
            )
        self.symbols: list[str] = list(symbols)
        self.timeframe: str = timeframe
        self.child_params: dict[str, Any] = dict(child_params or {})

        length = int(self.child_params.get("length", 26))
        distance = float(str(self.child_params.get("distance", 1.5)))
        smoothing = int(self.child_params.get("smoothing", 5))
        volatility_window = int(self.child_params.get("volatility_window", 200))
        self._frama = FRAMAChannel(
            length=length,
            distance=distance,
            smoothing=smoothing,
            volatility_window=volatility_window,
        )

        self._children: dict[str, FRAMAChannelStrategy] = {}
        for sym in self.symbols:
            try:
                child = FRAMAChannelStrategy(**self.child_params)
            except TypeError as e:
                raise ConfigError(
                    f"child_params do not match FRAMAChannelStrategy signature: {e}"
                ) from e
            # Share the wrapper's indicator so IndicatorEngine sees a single
            # FRAMA instance per (symbol, tf) and not N duplicates.
            child._frama = self._frama
            self._children[sym] = child

    def required_indicators(self) -> list[Indicator]:
        return [self._frama]

    def _swap_ctx(self, ctx: StrategyContext, symbol: str) -> StrategyContext:
        return replace(
            ctx,
            primary_symbol=symbol,
            primary_timeframe=self.timeframe,
        )

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        out: list[OrderIntent] = []
        for sym in self.symbols:
            child = self._children[sym]
            out.extend(child.on_bar(self._swap_ctx(ctx, sym)))
        return out

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: tuple[OrderView, ...],
    ) -> list[OrderAction]:
        out: list[OrderAction] = []
        for sym in self.symbols:
            child = self._children[sym]
            sym_pending = tuple(o for o in pending if o.symbol == sym)
            out.extend(child.on_pending_orders(self._swap_ctx(ctx, sym), sym_pending))
        return out
