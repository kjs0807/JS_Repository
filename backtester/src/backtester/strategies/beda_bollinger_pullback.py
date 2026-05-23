"""Beda Band + Bollinger mid pullback strategy.

Design:
- Beda bull/bear start creates a one-bar pending setup.
- On the following closed bar, validate that bar's open against the signal
  bar's Bollinger mid.  This avoids peeking at the next open before the engine
  can know it, at the cost of entering one bar later.
- Protective SL is the Beda start candle low/high.
- Indicator exits close on RSI target or opposite Beda start.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from backtester.core.context import OrderView, StrategyContext
from backtester.core.orders import (
    BracketSpec,
    ClosePosition,
    OrderAction,
    OrderIntent,
    TargetMarginPct,
)
from backtester.indicators.base import Indicator
from backtester.indicators.stateful.beda import BedaBand
from backtester.indicators.stateless.bb import BollingerBands
from backtester.strategies.base import BaseStrategy

Side = Literal["buy", "sell"]


@dataclass
class _PendingSetup:
    side: Side
    signal_bar_count: int
    signal_mid: float
    signal_close: float
    stop_price: Decimal


class BedaBollingerPullbackStrategy(BaseStrategy):
    """Beda start plus Bollinger-mid pullback strategy."""

    def __init__(
        self,
        *,
        rsi_length: int = 13,
        atr_period: int = 14,
        slow_mult: float = 2.0,
        fast_mult: float = 1.0,
        bb_period: int = 20,
        bb_std: float = 2.0,
        long_rsi_take_profit: float = 65.0,
        short_rsi_take_profit: float = 35.0,
        long_signal_rsi_max: float | None = None,
        short_signal_rsi_min: float | None = None,
        long_entry_rsi_max: float | None = None,
        short_entry_rsi_min: float | None = None,
        require_signal_close_below_mid_long: bool = False,
        require_signal_close_above_mid_short: bool = False,
        require_entry_close_above_signal_close_long: bool = False,
        require_entry_close_below_signal_close_short: bool = False,
        max_stop_distance_pct: Decimal | float | str | None = None,
        min_stop_distance_pct: Decimal | float | str | None = None,
        take_profit_pct: Decimal | float | str | None = None,
        exit_on_opposite_start: bool = True,
        time_stop_bars: int | None = None,
        allow_long: bool = True,
        allow_short: bool = True,
        margin_pct: Decimal | float | str = Decimal("0.05"),
        leverage: Decimal | float | str = Decimal("3"),
    ) -> None:
        self._beda = BedaBand(
            rsi_length=rsi_length,
            atr_period=atr_period,
            slow_mult=slow_mult,
            fast_mult=fast_mult,
        )
        self._bb = BollingerBands(period=bb_period, num_std=bb_std)
        if not 0 < long_rsi_take_profit <= 100:
            raise ValueError(
                "long_rsi_take_profit must be in (0, 100], "
                f"got {long_rsi_take_profit}"
            )
        if not 0 <= short_rsi_take_profit < 100:
            raise ValueError(
                "short_rsi_take_profit must be in [0, 100), "
                f"got {short_rsi_take_profit}"
            )
        self.long_rsi_take_profit = float(long_rsi_take_profit)
        self.short_rsi_take_profit = float(short_rsi_take_profit)
        self.long_signal_rsi_max = (
            None if long_signal_rsi_max is None else float(long_signal_rsi_max)
        )
        self.short_signal_rsi_min = (
            None if short_signal_rsi_min is None else float(short_signal_rsi_min)
        )
        self.long_entry_rsi_max = (
            None if long_entry_rsi_max is None else float(long_entry_rsi_max)
        )
        self.short_entry_rsi_min = (
            None if short_entry_rsi_min is None else float(short_entry_rsi_min)
        )
        self.require_signal_close_below_mid_long = require_signal_close_below_mid_long
        self.require_signal_close_above_mid_short = require_signal_close_above_mid_short
        self.require_entry_close_above_signal_close_long = (
            require_entry_close_above_signal_close_long
        )
        self.require_entry_close_below_signal_close_short = (
            require_entry_close_below_signal_close_short
        )
        self.max_stop_distance_pct = (
            None
            if max_stop_distance_pct is None
            else Decimal(str(max_stop_distance_pct))
        )
        self.min_stop_distance_pct = (
            None
            if min_stop_distance_pct is None
            else Decimal(str(min_stop_distance_pct))
        )
        self.take_profit_pct = (
            None if take_profit_pct is None else Decimal(str(take_profit_pct))
        )
        self.exit_on_opposite_start = exit_on_opposite_start
        self.time_stop_bars = (
            None if time_stop_bars is None or time_stop_bars <= 0 else time_stop_bars
        )
        self.allow_long = allow_long
        self.allow_short = allow_short
        self.margin_pct = Decimal(str(margin_pct))
        self.leverage = Decimal(str(leverage))
        if self.margin_pct <= 0:
            raise ValueError(f"margin_pct must be > 0, got {self.margin_pct}")
        if self.leverage <= 0:
            raise ValueError(f"leverage must be > 0, got {self.leverage}")
        self._pending: dict[str, _PendingSetup] = {}

    def required_indicators(self) -> list[Indicator]:
        return [self._beda, self._bb]

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        symbol = ctx.primary_symbol
        tf = ctx.primary_timeframe
        bars = ctx.bars[symbol][tf]
        if bars.height < 2:
            return []

        ind = ctx.indicators[symbol][tf]
        if ind.height == 0:
            return []

        idx = bars.height - 1
        prefix = self._beda.name
        bb_prefix = self._bb.name
        rsi = ind[f"{prefix}_rsi"][idx]
        bull_start = bool(ind[f"{prefix}_bull_start"][idx] or False)
        bear_start = bool(ind[f"{prefix}_bear_start"][idx] or False)

        if ctx.has_position(symbol):
            self._pending.pop(symbol, None)
            pos = ctx.position(symbol)
            if pos is None:
                return []
            if self.time_stop_bars is not None:
                held = ctx.bars_held(symbol)
                if held is not None and held >= self.time_stop_bars:
                    side: Side = "sell" if pos.size > 0 else "buy"
                    return [self._close(symbol, side, "beda_bb_time_stop")]
            if pos.size > 0 and (
                (rsi is not None and float(rsi) >= self.long_rsi_take_profit)
                or (self.exit_on_opposite_start and bear_start)
            ):
                return [self._close(symbol, "sell", "beda_bb_long_exit")]
            if pos.size < 0 and (
                (rsi is not None and float(rsi) <= self.short_rsi_take_profit)
                or (self.exit_on_opposite_start and bull_start)
            ):
                return [self._close(symbol, "buy", "beda_bb_short_exit")]
            return []

        pending_intent = self._maybe_fire_pending(symbol, bars, ind, idx)
        if pending_intent is not None:
            return [pending_intent]

        self._capture_new_setup(
            symbol=symbol,
            bars=bars,
            ind=ind,
            idx=idx,
            bull_start=bull_start,
            bear_start=bear_start,
            rsi=None if rsi is None else float(rsi),
            bb_mid_col=f"{bb_prefix}_mid",
        )
        return []

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: tuple[OrderView, ...],
    ) -> list[OrderAction]:
        if ctx.has_position(ctx.primary_symbol):
            return []
        return [
            OrderAction(type="cancel", order_id=o.id)
            for o in pending
            if o.symbol == ctx.primary_symbol and o.bracket_role is not None
        ]

    def _maybe_fire_pending(
        self,
        symbol: str,
        bars,
        ind,
        idx: int,
    ) -> OrderIntent | None:
        setup = self._pending.get(symbol)
        if setup is None:
            return None
        current_bar_count = idx + 1
        if current_bar_count <= setup.signal_bar_count:
            return None
        self._pending.pop(symbol, None)
        if current_bar_count != setup.signal_bar_count + 1:
            return None

        current_open = bars["open"][idx]
        current_close = bars["close"][idx]
        rsi = ind[f"{self._beda.name}_rsi"][idx]
        if current_open is None:
            return None
        if setup.side == "buy":
            if float(current_open) >= setup.signal_mid:
                return None
            if (
                self.long_entry_rsi_max is not None
                and rsi is not None
                and float(rsi) > self.long_entry_rsi_max
            ):
                return None
            if (
                self.require_entry_close_above_signal_close_long
                and current_close is not None
                and float(current_close) <= setup.signal_close
            ):
                return None
            reason = "beda_bb_bull_start_mid_pullback"
        else:
            if float(current_open) <= setup.signal_mid:
                return None
            if (
                self.short_entry_rsi_min is not None
                and rsi is not None
                and float(rsi) < self.short_entry_rsi_min
            ):
                return None
            if (
                self.require_entry_close_below_signal_close_short
                and current_close is not None
                and float(current_close) >= setup.signal_close
            ):
                return None
            reason = "beda_bb_bear_start_mid_pullback"

        entry_ref = current_close
        client_suffix = "na" if entry_ref is None else str(entry_ref)
        if setup.stop_price <= 0:
            return None
        if entry_ref is not None and not self._stop_distance_ok(
            Decimal(str(entry_ref)), setup.stop_price, setup.side
        ):
            return None
        bracket = self._build_bracket(
            entry_price=None if entry_ref is None else Decimal(str(entry_ref)),
            stop_price=setup.stop_price,
            side=setup.side,
        )
        return OrderIntent(
            symbol=symbol,
            side=setup.side,
            type="market",
            size_spec=TargetMarginPct(
                margin_pct=self.margin_pct,
                leverage=self.leverage,
            ),
            reason=reason,
            bracket=bracket,
            client_order_id=f"{reason}:{current_bar_count}:{client_suffix}",
        )

    def _capture_new_setup(
        self,
        *,
        symbol: str,
        bars,
        ind,
        idx: int,
        bull_start: bool,
        bear_start: bool,
        rsi: float | None,
        bb_mid_col: str,
    ) -> None:
        mid = ind[bb_mid_col][idx]
        if mid is None:
            return
        close = bars["close"][idx]
        if bull_start and self.allow_long:
            if self.long_signal_rsi_max is not None and (
                rsi is None or rsi > self.long_signal_rsi_max
            ):
                return
            if (
                self.require_signal_close_below_mid_long
                and close is not None
                and float(close) >= float(mid)
            ):
                return
            low = bars["low"][idx]
            if low is not None:
                self._pending[symbol] = _PendingSetup(
                    side="buy",
                    signal_bar_count=idx + 1,
                    signal_mid=float(mid),
                    signal_close=float(close) if close is not None else float(mid),
                    stop_price=Decimal(str(low)),
                )
        elif bear_start and self.allow_short:
            if self.short_signal_rsi_min is not None and (
                rsi is None or rsi < self.short_signal_rsi_min
            ):
                return
            if (
                self.require_signal_close_above_mid_short
                and close is not None
                and float(close) <= float(mid)
            ):
                return
            high = bars["high"][idx]
            if high is not None:
                self._pending[symbol] = _PendingSetup(
                    side="sell",
                    signal_bar_count=idx + 1,
                    signal_mid=float(mid),
                    signal_close=float(close) if close is not None else float(mid),
                    stop_price=Decimal(str(high)),
                )

    def _stop_distance_ok(
        self,
        entry_price: Decimal,
        stop_price: Decimal,
        side: Side,
    ) -> bool:
        if entry_price <= 0:
            return False
        distance = (
            (entry_price - stop_price) / entry_price
            if side == "buy"
            else (stop_price - entry_price) / entry_price
        )
        if distance <= 0:
            return False
        if self.min_stop_distance_pct is not None and distance < self.min_stop_distance_pct:
            return False
        if self.max_stop_distance_pct is not None and distance > self.max_stop_distance_pct:
            return False
        return True

    def _build_bracket(
        self,
        *,
        entry_price: Decimal | None,
        stop_price: Decimal,
        side: Side,
    ) -> BracketSpec:
        take_profit_price: Decimal | None = None
        if entry_price is not None and self.take_profit_pct is not None:
            take_profit_price = (
                entry_price * (Decimal("1") + self.take_profit_pct)
                if side == "buy"
                else entry_price * (Decimal("1") - self.take_profit_pct)
            )
        return BracketSpec(
            take_profit_price=take_profit_price,
            stop_loss_price=stop_price,
            time_stop_bars=self.time_stop_bars,
        )

    @staticmethod
    def _close(symbol: str, side: Side, reason: str) -> OrderIntent:
        return OrderIntent(
            symbol=symbol,
            side=side,
            type="market",
            size_spec=ClosePosition(),
            reason=reason,
            reduce_only=True,
        )


__all__ = ["BedaBollingerPullbackStrategy"]
