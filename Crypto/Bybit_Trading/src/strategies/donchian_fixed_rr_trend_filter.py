"""Donchian FixedRR + EMA trend filter (Variant C+T).

This is NOT a new strategy. It is DonchianFixedRR with the EMA(200)
trend filter from DonchianTrendFilter restored. The baseline FixedRR
variant has no regime gate and takes every breakout in both directions,
which absorbs counter-trend noise. Porting the proven EMA filter from
the TrendFilter variant is a safety-feature restoration, not a new
experiment.

Behavior parity:
    - Entry: close > donchian(entry_period).upper AND close > ema(ema_filter)  -> LONG
             close < donchian(entry_period).lower AND close < ema(ema_filter)  -> SHORT
    - Exit: unchanged from DonchianFixedRR (ATR stop + fixed RR TP +
            ATR trailing after activation). Exit logic does not consult
            the EMA -- once in, the position runs on its original plan.
    - Sizing: unchanged (2% risk / stop_distance, broker-managed)

Inherits from DonchianFixedRR and overrides only ``prepare`` /
``on_bar_fast`` / ``on_bar`` so the trailing stop helper and all other
mechanics come from the parent class unchanged.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from src.core.types import Bar, BarSeries
from src.execution.broker import Broker
from src.strategies.base import IndicatorCache
from src.strategies.donchian_fixed_rr import DonchianFixedRR
from src.strategies.indicators.channel import donchian
from src.strategies.indicators.momentum import atr
from src.strategies.indicators.trend import ema


class DonchianFixedRRTrendFilter(DonchianFixedRR):
    """DonchianFixedRR + EMA(ema_filter) trend gate on entry."""

    name: str = "Donchian_FixedRR_TrendFilter"

    def __init__(
        self,
        entry_period: int = 20,
        atr_period: int = 14,
        stop_atr: float = 2.5,
        tp_r_ratio: float = 2.0,
        trail_activate_atr: float = 1.5,
        trail_distance_atr: float = 1.0,
        ema_filter: int = 200,
        timeframe: str = "1h",
    ) -> None:
        super().__init__(
            entry_period=entry_period,
            atr_period=atr_period,
            stop_atr=stop_atr,
            tp_r_ratio=tp_r_ratio,
            trail_activate_atr=trail_activate_atr,
            trail_distance_atr=trail_distance_atr,
            timeframe=timeframe,
        )
        self.ema_filter = ema_filter

    @property
    def warmup_bars(self) -> int:
        # Need enough bars for EMA(ema_filter) on top of parent warmup
        return max(self.entry_period, self.atr_period, self.ema_filter) + 1

    def prepare(self, full_series: BarSeries) -> IndicatorCache:
        # Parent computes donchian upper/lower + atr
        cache = super().prepare(full_series)
        # Add the trend filter EMA
        ema_r = ema(full_series, period=self.ema_filter)
        cache.arrays["ema"] = ema_r.values
        return cache

    def on_bar(self, bar: Bar, series: BarSeries, broker: Broker) -> None:
        """Legacy slow path -- recomputes indicators every call. Used by
        unit tests that do not pre-prepare a cache. Mirrors the parent's
        shape but adds the EMA filter inline."""
        if len(series) < self.warmup_bars:
            return
        ch = donchian(series, period=self.entry_period)
        atr_r = atr(series, period=self.atr_period)
        ema_r = ema(series, period=self.ema_filter)
        upper = ch.upper[-1]
        lower = ch.lower[-1]
        atr_val = atr_r.values[-1]
        ema_val = ema_r.values[-1]
        if any(np.isnan(x) for x in [upper, lower, atr_val, ema_val]):
            return
        if atr_val <= 0:
            return

        close = bar.close
        pos = broker.get_position(bar.symbol)

        if pos is not None:
            self._update_trailing(pos, close, atr_val, broker)
            return

        if close > upper and close > ema_val:
            sl = close - self.stop_atr * atr_val
            tp = close + (self.stop_atr * self.tp_r_ratio) * atr_val
            qty = broker.calc_qty(
                bar.symbol, risk_pct=0.02, stop_distance=close - sl,
            )
            if qty > 0:
                broker.buy(
                    bar.symbol, qty, stop_loss=sl, take_profit=tp,
                    reason=(
                        f"Donchian {self.entry_period} LONG "
                        f"RR1:{self.tp_r_ratio} + EMA{self.ema_filter}"
                    ),
                )
        elif close < lower and close < ema_val:
            sl = close + self.stop_atr * atr_val
            tp = close - (self.stop_atr * self.tp_r_ratio) * atr_val
            qty = broker.calc_qty(
                bar.symbol, risk_pct=0.02, stop_distance=sl - close,
            )
            if qty > 0:
                broker.sell(
                    bar.symbol, qty, stop_loss=sl, take_profit=tp,
                    reason=(
                        f"Donchian {self.entry_period} SHORT "
                        f"RR1:{self.tp_r_ratio} + EMA{self.ema_filter}"
                    ),
                )

    def on_bar_fast(self, bar: Bar, i: int, cache, broker) -> None:
        upper = cache.arrays["upper"][i]
        lower = cache.arrays["lower"][i]
        atr_val = cache.arrays["atr"][i]
        ema_val = cache.arrays["ema"][i]

        if any(np.isnan(x) for x in [upper, lower, atr_val, ema_val]):
            return
        if atr_val <= 0:
            return

        close = bar.close
        pos = broker.get_position(bar.symbol)

        if pos is not None:
            self._update_trailing(pos, close, atr_val, broker)
            return

        if close > upper and close > ema_val:
            sl = close - self.stop_atr * atr_val
            tp = close + (self.stop_atr * self.tp_r_ratio) * atr_val
            qty = broker.calc_qty(
                bar.symbol, risk_pct=0.02, stop_distance=close - sl,
            )
            if qty > 0:
                broker.buy(
                    bar.symbol, qty, stop_loss=sl, take_profit=tp,
                    reason=(
                        f"Donchian {self.entry_period} LONG "
                        f"RR1:{self.tp_r_ratio} + EMA{self.ema_filter}"
                    ),
                )
        elif close < lower and close < ema_val:
            sl = close + self.stop_atr * atr_val
            tp = close - (self.stop_atr * self.tp_r_ratio) * atr_val
            qty = broker.calc_qty(
                bar.symbol, risk_pct=0.02, stop_distance=sl - close,
            )
            if qty > 0:
                broker.sell(
                    bar.symbol, qty, stop_loss=sl, take_profit=tp,
                    reason=(
                        f"Donchian {self.entry_period} SHORT "
                        f"RR1:{self.tp_r_ratio} + EMA{self.ema_filter}"
                    ),
                )

    def get_params(self) -> dict:
        params = super().get_params()
        params["ema_filter"] = self.ema_filter
        return params


__all__ = ["DonchianFixedRRTrendFilter"]
