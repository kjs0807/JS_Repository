"""FRAMA Channel strategy with EMA regime filter.

This variant keeps the strategy rule intentionally small:

- close > EMA(period) + FRAMA break up + flat -> long entry
- close < EMA(period) + FRAMA break down + flat -> short entry
- long exits on FRAMA break down
- short exits on FRAMA break up

No fixed TP is attached by default; exits are driven by the opposite FRAMA
signal. An optional hard stop can be enabled with ``sl_pct`` if desired.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any, Literal

from backtester.core.context import OrderView, StrategyContext
from backtester.core.errors import ConfigError
from backtester.core.orders import (
    BracketSpec,
    ClosePosition,
    OrderAction,
    OrderIntent,
    TargetMarginPct,
)
from backtester.indicators.base import Indicator
from backtester.indicators.stateful.frama import FRAMAChannel
from backtester.indicators.stateless.ema import EMA
from backtester.strategies.base import BaseStrategy


class FRAMAEMA200ChannelStrategy(BaseStrategy):
    """Single-symbol FRAMA breakout filtered by EMA regime."""

    def __init__(
        self,
        *,
        length: int = 26,
        distance: Decimal | float | str = Decimal("1.5"),
        smoothing: int = 5,
        volatility_window: int = 200,
        ema_period: int = 200,
        timeframe: str = "1h",
        allow_short: bool = True,
        margin_pct: Decimal | float | str = Decimal("0.03"),
        leverage: Decimal | float | str = Decimal("3"),
        sl_pct: Decimal | float | str | None = None,
    ) -> None:
        self._frama = FRAMAChannel(
            length=length,
            distance=float(Decimal(str(distance))),
            smoothing=smoothing,
            volatility_window=volatility_window,
        )
        self._ema = EMA(period=ema_period)
        self.timeframe = timeframe
        self.allow_short = allow_short
        self.margin_pct = Decimal(str(margin_pct))
        self.leverage = Decimal(str(leverage))
        if self.margin_pct <= 0:
            raise ValueError(f"margin_pct must be > 0, got {self.margin_pct}")
        if self.leverage <= 0:
            raise ValueError(f"leverage must be > 0, got {self.leverage}")
        self.sl_pct = Decimal(str(sl_pct)) if sl_pct is not None else None
        if self.sl_pct is not None and self.sl_pct <= 0:
            raise ValueError(f"sl_pct must be > 0 when set, got {self.sl_pct}")

    def required_indicators(self) -> list[Indicator]:
        return [self._frama, self._ema]

    def _build_stop(
        self,
        entry_price: Decimal,
        side: Literal["buy", "sell"],
    ) -> BracketSpec | None:
        if self.sl_pct is None:
            return None
        price_sl = self.sl_pct / self.leverage
        stop = (
            entry_price * (Decimal("1") - price_sl)
            if side == "buy"
            else entry_price * (Decimal("1") + price_sl)
        )
        return BracketSpec(stop_loss_price=stop)

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        symbol = ctx.primary_symbol
        tf = ctx.primary_timeframe
        bars = ctx.bars[symbol][tf]
        if bars.height < 2:
            return []

        ind_df = ctx.indicators[symbol][tf]
        if ind_df.height == 0:
            return []
        last_idx = ind_df.height - 1
        close_val = bars["close"][last_idx]
        ema_val = ind_df[self._ema.name][last_idx]
        break_up = ind_df["frama_break_up"][last_idx]
        break_dn = ind_df["frama_break_dn"][last_idx]
        if close_val is None or ema_val is None:
            return []

        pos = ctx.position(symbol)
        if pos is not None and not pos.is_flat:
            if pos.size > 0 and break_dn:
                return [
                    OrderIntent(
                        symbol=symbol,
                        side="sell",
                        type="market",
                        size_spec=ClosePosition(),
                        reason="frama_ema200_exit_long_break_dn",
                        reduce_only=True,
                    )
                ]
            if pos.size < 0 and break_up:
                return [
                    OrderIntent(
                        symbol=symbol,
                        side="buy",
                        type="market",
                        size_spec=ClosePosition(),
                        reason="frama_ema200_exit_short_break_up",
                        reduce_only=True,
                    )
                ]
            return []

        side: Literal["buy", "sell"] | None = None
        reason = ""
        if break_up and close_val > ema_val:
            side = "buy"
            reason = "frama_ema200_long_break_up"
        elif self.allow_short and break_dn and close_val < ema_val:
            side = "sell"
            reason = "frama_ema200_short_break_dn"
        if side is None:
            return []

        entry_price = Decimal(str(close_val))
        return [
            OrderIntent(
                symbol=symbol,
                side=side,
                type="market",
                size_spec=TargetMarginPct(
                    margin_pct=self.margin_pct,
                    leverage=self.leverage,
                ),
                reason=reason,
                bracket=self._build_stop(entry_price, side),
            )
        ]


class FRAMAMultiEMA200ChannelStrategy(BaseStrategy):
    """Run ``FRAMAEMA200ChannelStrategy`` across multiple symbols."""

    def __init__(
        self,
        *,
        symbols: list[str],
        timeframe: str = "1h",
        child_params: dict[str, Any] | None = None,
    ) -> None:
        if not symbols:
            raise ConfigError(
                "FRAMAMultiEMA200ChannelStrategy requires non-empty 'symbols' list"
            )
        if len(set(symbols)) != len(symbols):
            raise ConfigError(
                f"FRAMAMultiEMA200ChannelStrategy 'symbols' contains duplicates: {symbols}"
            )
        self.symbols = list(symbols)
        self.timeframe = timeframe
        self.child_params = dict(child_params or {})

        length = int(self.child_params.get("length", 26))
        distance = float(str(self.child_params.get("distance", 1.5)))
        smoothing = int(self.child_params.get("smoothing", 5))
        volatility_window = int(self.child_params.get("volatility_window", 200))
        ema_period = int(self.child_params.get("ema_period", 200))
        self._frama = FRAMAChannel(
            length=length,
            distance=distance,
            smoothing=smoothing,
            volatility_window=volatility_window,
        )
        self._ema = EMA(period=ema_period)

        self._children: dict[str, FRAMAEMA200ChannelStrategy] = {}
        for sym in self.symbols:
            try:
                child = FRAMAEMA200ChannelStrategy(**self.child_params)
            except TypeError as e:
                raise ConfigError(
                    "child_params do not match "
                    f"FRAMAEMA200ChannelStrategy signature: {e}"
                ) from e
            child._frama = self._frama
            child._ema = self._ema
            self._children[sym] = child

    def required_indicators(self) -> list[Indicator]:
        return [self._frama, self._ema]

    def _swap_ctx(self, ctx: StrategyContext, symbol: str) -> StrategyContext:
        return replace(
            ctx,
            primary_symbol=symbol,
            primary_timeframe=self.timeframe,
        )

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        out: list[OrderIntent] = []
        for sym in self.symbols:
            out.extend(self._children[sym].on_bar(self._swap_ctx(ctx, sym)))
        return out

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: tuple[OrderView, ...],
    ) -> list[OrderAction]:
        out: list[OrderAction] = []
        for sym in self.symbols:
            sym_pending = tuple(o for o in pending if o.symbol == sym)
            out.extend(
                self._children[sym].on_pending_orders(
                    self._swap_ctx(ctx, sym),
                    sym_pending,
                )
            )
        return out


__all__ = ["FRAMAEMA200ChannelStrategy", "FRAMAMultiEMA200ChannelStrategy"]
