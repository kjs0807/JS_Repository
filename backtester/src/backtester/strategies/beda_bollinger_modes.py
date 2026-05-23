"""Beda Band + Bollinger experimental strategy modes.

This module is intentionally research-oriented.  It keeps the execution
contract simple (market entries, fixed TP/SL/time-stop brackets) while varying
where Beda participates: entry filter, exit signal, breakout confirmation,
contrarian signal, or higher-timeframe filter.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from backtester.core.context import StrategyContext
from backtester.core.orders import BracketSpec, ClosePosition, OrderIntent, TargetMarginPct
from backtester.indicators.base import Indicator
from backtester.indicators.stateful.beda import BedaBand
from backtester.indicators.stateless.bb import BollingerBands
from backtester.strategies.base import BaseStrategy

Mode = Literal[
    "bb_reentry_beda_filter",
    "bb_reentry_beda_exit",
    "bb_breakout_beda_trend",
    "beda_start_contrarian",
    "mtf_bb_reentry_beda_filter",
]
Side = Literal["buy", "sell"]


@dataclass
class _Pending:
    side: Side
    signal_count: int
    signal_mid: float


class BedaBollingerModesStrategy(BaseStrategy):
    """Research strategy with multiple Beda/Bollinger combinations."""

    def __init__(
        self,
        *,
        mode: Mode = "bb_reentry_beda_filter",
        rsi_length: int = 13,
        atr_period: int = 14,
        slow_mult: float = 2.0,
        fast_mult: float = 1.0,
        bb_period: int = 20,
        bb_std: float = 2.0,
        reentry_lookback: int = 5,
        filter_timeframe: str = "5m",
        long_rsi_min: float | None = 50.0,
        short_rsi_max: float | None = 50.0,
        long_rsi_take_profit: float = 65.0,
        short_rsi_take_profit: float = 35.0,
        take_profit_pct: Decimal | float | str | None = Decimal("0.006"),
        stop_loss_pct: Decimal | float | str | None = Decimal("0.004"),
        time_stop_bars: int | None = 24,
        allow_long: bool = True,
        allow_short: bool = True,
        margin_pct: Decimal | float | str = Decimal("0.03"),
        leverage: Decimal | float | str = Decimal("3"),
    ) -> None:
        self.mode = mode
        self._beda = BedaBand(
            rsi_length=rsi_length,
            atr_period=atr_period,
            slow_mult=slow_mult,
            fast_mult=fast_mult,
        )
        self._bb = BollingerBands(period=bb_period, num_std=bb_std)
        self.reentry_lookback = max(1, int(reentry_lookback))
        self.filter_timeframe = filter_timeframe
        self.long_rsi_min = None if long_rsi_min is None else float(long_rsi_min)
        self.short_rsi_max = None if short_rsi_max is None else float(short_rsi_max)
        self.long_rsi_take_profit = float(long_rsi_take_profit)
        self.short_rsi_take_profit = float(short_rsi_take_profit)
        self.take_profit_pct = None if take_profit_pct is None else Decimal(str(take_profit_pct))
        self.stop_loss_pct = None if stop_loss_pct is None else Decimal(str(stop_loss_pct))
        self.time_stop_bars = None if time_stop_bars is None or time_stop_bars <= 0 else int(time_stop_bars)
        self.allow_long = allow_long
        self.allow_short = allow_short
        self.margin_pct = Decimal(str(margin_pct))
        self.leverage = Decimal(str(leverage))
        self._pending: dict[str, _Pending] = {}

    def required_indicators(self) -> list[Indicator]:
        return [self._beda, self._bb]

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        symbol = ctx.primary_symbol
        tf = ctx.primary_timeframe
        bars = ctx.bars[symbol][tf]
        if bars.height < max(3, self.reentry_lookback + 2):
            return []
        ind = ctx.indicators[symbol][tf]
        idx = bars.height - 1

        exit_intent = self._maybe_exit(ctx, symbol, bars, ind, idx)
        if exit_intent is not None:
            return [exit_intent]
        if ctx.has_position(symbol):
            return []

        side: Side | None = None
        if self.mode == "bb_reentry_beda_filter":
            side = self._bb_reentry_side(bars, ind, idx, use_beda_filter=True)
        elif self.mode == "bb_reentry_beda_exit":
            side = self._bb_reentry_side(bars, ind, idx, use_beda_filter=False)
        elif self.mode == "bb_breakout_beda_trend":
            side = self._bb_breakout_side(bars, ind, idx)
        elif self.mode == "beda_start_contrarian":
            side = self._contrarian_pending_side(symbol, bars, ind, idx)
        elif self.mode == "mtf_bb_reentry_beda_filter":
            side = self._bb_reentry_side(bars, ind, idx, use_beda_filter=False)
            if side is not None and not self._mtf_filter_ok(ctx, symbol, side):
                side = None
        else:
            raise ValueError(f"unknown mode: {self.mode!r}")

        if side is None:
            return []
        close = bars["close"][idx]
        if close is None:
            return []
        return [
            OrderIntent(
                symbol=symbol,
                side=side,
                type="market",
                size_spec=TargetMarginPct(self.margin_pct, self.leverage),
                reason=f"beda_modes_{self.mode}_{'long' if side == 'buy' else 'short'}",
                bracket=self._bracket(Decimal(str(close)), side),
            )
        ]

    def _maybe_exit(
        self,
        ctx: StrategyContext,
        symbol: str,
        bars,
        ind,
        idx: int,
    ) -> OrderIntent | None:
        pos = ctx.position(symbol)
        if pos is None or pos.is_flat:
            return None
        if self.time_stop_bars is not None:
            held = ctx.bars_held(symbol)
            if held is not None and held >= self.time_stop_bars:
                return self._close(symbol, "sell" if pos.size > 0 else "buy", "beda_modes_time_stop")

        prefix = self._beda.name
        bb = self._bb.name
        rsi = ind[f"{prefix}_rsi"][idx]
        bull_start = bool(ind[f"{prefix}_bull_start"][idx] or False)
        bear_start = bool(ind[f"{prefix}_bear_start"][idx] or False)
        close = bars["close"][idx]
        mid = ind[f"{bb}_mid"][idx]

        if pos.size > 0:
            if rsi is not None and float(rsi) >= self.long_rsi_take_profit:
                return self._close(symbol, "sell", "beda_modes_rsi_tp")
            if bear_start:
                return self._close(symbol, "sell", "beda_modes_opposite_start")
            if self.mode == "bb_reentry_beda_exit" and close is not None and mid is not None and close >= mid:
                return self._close(symbol, "sell", "beda_modes_mid_exit")
        else:
            if rsi is not None and float(rsi) <= self.short_rsi_take_profit:
                return self._close(symbol, "buy", "beda_modes_rsi_tp")
            if bull_start:
                return self._close(symbol, "buy", "beda_modes_opposite_start")
            if self.mode == "bb_reentry_beda_exit" and close is not None and mid is not None and close <= mid:
                return self._close(symbol, "buy", "beda_modes_mid_exit")
        return None

    def _bb_reentry_side(self, bars, ind, idx: int, *, use_beda_filter: bool) -> Side | None:
        bb = self._bb.name
        close = bars["close"][idx]
        prev_close = bars["close"][idx - 1]
        lower = ind[f"{bb}_lower"][idx]
        upper = ind[f"{bb}_upper"][idx]
        if close is None or prev_close is None or lower is None or upper is None:
            return None

        recent_lowers = ind[f"{bb}_lower"].slice(max(0, idx - self.reentry_lookback), self.reentry_lookback).to_list()
        recent_uppers = ind[f"{bb}_upper"].slice(max(0, idx - self.reentry_lookback), self.reentry_lookback).to_list()
        recent_closes = bars["close"].slice(max(0, idx - self.reentry_lookback), self.reentry_lookback).to_list()
        had_lower_break = any(c is not None and l is not None and c < l for c, l in zip(recent_closes, recent_lowers))
        had_upper_break = any(c is not None and u is not None and c > u for c, u in zip(recent_closes, recent_uppers))

        if self.allow_long and had_lower_break and prev_close <= lower and close > lower:
            if not use_beda_filter or self._beda_ok(ind, idx, "buy"):
                return "buy"
        if self.allow_short and had_upper_break and prev_close >= upper and close < upper:
            if not use_beda_filter or self._beda_ok(ind, idx, "sell"):
                return "sell"
        return None

    def _bb_breakout_side(self, bars, ind, idx: int) -> Side | None:
        bb = self._bb.name
        close = bars["close"][idx]
        prev_close = bars["close"][idx - 1]
        upper = ind[f"{bb}_upper"][idx]
        lower = ind[f"{bb}_lower"][idx]
        if close is None or prev_close is None or upper is None or lower is None:
            return None
        if self.allow_long and prev_close <= upper and close > upper and self._beda_ok(ind, idx, "buy"):
            return "buy"
        if self.allow_short and prev_close >= lower and close < lower and self._beda_ok(ind, idx, "sell"):
            return "sell"
        return None

    def _contrarian_pending_side(self, symbol: str, bars, ind, idx: int) -> Side | None:
        prefix = self._beda.name
        bb = self._bb.name
        pending = self._pending.get(symbol)
        if pending is not None and idx + 1 == pending.signal_count + 1:
            self._pending.pop(symbol, None)
            open_ = bars["open"][idx]
            if open_ is None:
                return None
            if pending.side == "sell" and float(open_) < pending.signal_mid and self.allow_short:
                return "sell"
            if pending.side == "buy" and float(open_) > pending.signal_mid and self.allow_long:
                return "buy"
            return None

        mid = ind[f"{bb}_mid"][idx]
        if mid is None:
            return None
        if bool(ind[f"{prefix}_bull_start"][idx] or False):
            self._pending[symbol] = _Pending("sell", idx + 1, float(mid))
        elif bool(ind[f"{prefix}_bear_start"][idx] or False):
            self._pending[symbol] = _Pending("buy", idx + 1, float(mid))
        return None

    def _mtf_filter_ok(self, ctx: StrategyContext, symbol: str, side: Side) -> bool:
        if not ctx.indicators.has(symbol, self.filter_timeframe):
            return False
        ind = ctx.indicators[symbol][self.filter_timeframe]
        if ind.height == 0:
            return False
        return self._beda_ok(ind, ind.height - 1, side)

    def _beda_ok(self, ind, idx: int, side: Side) -> bool:
        prefix = self._beda.name
        rsi = ind[f"{prefix}_rsi"][idx]
        if side == "buy":
            bull = bool(ind[f"{prefix}_bull"][idx] or False)
            rsi_ok = rsi is not None and (self.long_rsi_min is None or float(rsi) >= self.long_rsi_min)
            return bull and rsi_ok
        bear = bool(ind[f"{prefix}_bear"][idx] or False)
        rsi_ok = rsi is not None and (self.short_rsi_max is None or float(rsi) <= self.short_rsi_max)
        return bear and rsi_ok

    def _bracket(self, entry: Decimal, side: Side) -> BracketSpec | None:
        tp: Decimal | None = None
        sl: Decimal | None = None
        if self.take_profit_pct is not None:
            tp = entry * (Decimal("1") + self.take_profit_pct) if side == "buy" else entry * (Decimal("1") - self.take_profit_pct)
        if self.stop_loss_pct is not None:
            sl = entry * (Decimal("1") - self.stop_loss_pct) if side == "buy" else entry * (Decimal("1") + self.stop_loss_pct)
        if tp is None and sl is None and self.time_stop_bars is None:
            return None
        return BracketSpec(take_profit_price=tp, stop_loss_price=sl, time_stop_bars=self.time_stop_bars)

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


__all__ = ["BedaBollingerModesStrategy"]
