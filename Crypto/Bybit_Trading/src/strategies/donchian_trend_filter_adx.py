"""Donchian TrendFilter + ADX regime gate (Variant B+ADX).

DonchianTrendFilter with an additional ADX >= threshold gate on entry.
The hypothesis is that Donchian breakouts are most profitable when the
market is actually trending; in chop periods the breakout becomes a
false positive. ADX is the classic "is there a trend at all" indicator,
so gating entry on ADX >= N should reduce false-breakout losses.

This module exposes:

    DonchianTrendFilterADX     -- generic subclass with adjustable
                                  ``adx_min`` threshold
    DonchianTrendFilterADX20   -- thin wrapper pinned at adx_min=20
    DonchianTrendFilterADX25   -- thin wrapper pinned at adx_min=25

The two concrete variants exist so the explore/backtest pipeline and
tests can compare two regime thresholds side-by-side as a single
experiment (the "2-point regime cut" agreed in the improvement memo),
without sweeping arbitrary intermediate values.

Design choice: gate only entry, not exit. Positions already open are
still managed by the parent's exit logic (Donchian(exit_period) break
or EMA break), so ADX dropping below the threshold mid-trade does not
force an exit -- the trade simply runs on its original plan until
signaled.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from src.core.types import Bar, BarSeries
from src.execution.broker import Broker
from src.strategies.base import IndicatorCache
from src.strategies.donchian_trend_filter import DonchianTrendFilter
from src.strategies.indicators.channel import donchian
from src.strategies.indicators.momentum import adx, atr
from src.strategies.indicators.trend import ema


class DonchianTrendFilterADX(DonchianTrendFilter):
    """DonchianTrendFilter with an ADX regime gate on entry."""

    name: str = "Donchian_TrendFilter_ADX"

    def __init__(
        self,
        entry_period: int = 20,
        exit_period: int = 10,
        ema_filter: int = 200,
        atr_period: int = 14,
        stop_atr: float = 2.0,
        adx_period: int = 14,
        adx_min: float = 20.0,
        timeframe: str = "1h",
    ) -> None:
        super().__init__(
            entry_period=entry_period,
            exit_period=exit_period,
            ema_filter=ema_filter,
            atr_period=atr_period,
            stop_atr=stop_atr,
            timeframe=timeframe,
        )
        self.adx_period = adx_period
        self.adx_min = adx_min

    @property
    def warmup_bars(self) -> int:
        # ADX needs ~2x period before its first non-NaN (EMA-of-EMA)
        return (
            max(
                self.entry_period,
                self.exit_period,
                self.ema_filter,
                self.atr_period,
                self.adx_period * 2,
            )
            + 10
        )

    def prepare(self, full_series: BarSeries) -> IndicatorCache:
        cache = super().prepare(full_series)
        adx_r = adx(full_series, period=self.adx_period)
        cache.arrays["adx"] = adx_r.values
        return cache

    def on_bar(self, bar: Bar, series: BarSeries, broker: Broker) -> None:
        """Slow path for unit-test compatibility. Delegates to the fast
        path with a freshly prepared cache, matching the pattern used by
        BBKCSqueeze."""
        if len(series) < self.warmup_bars:
            return
        cache = self.prepare(series)
        idx = len(series) - 1
        self.on_bar_fast(bar, idx, cache, broker)

    def on_bar_fast(self, bar: Bar, i: int, cache, broker) -> None:
        """Entry is gated on ADX; exit logic is delegated unchanged to
        the parent so positions already open can still close on
        Donchian(exit)/EMA breaks even if ADX has since dropped."""
        pos = broker.get_position(bar.symbol)
        if pos is None:
            # Check ADX gate before allowing the parent's entry logic
            adx_arr = cache.arrays.get("adx")
            if adx_arr is None or i >= len(adx_arr):
                return
            adx_val = adx_arr[i]
            if np.isnan(adx_val) or adx_val < self.adx_min:
                return  # Gate: no entry in low-ADX (chop) regime
        # Delegate to parent (exit if pos exists, entry if pos is None
        # and ADX passed). Parent will re-check the position state but
        # that is cheap and keeps the two code paths in sync.
        super().on_bar_fast(bar, i, cache, broker)

    def get_params(self) -> dict:
        params = super().get_params()
        params["adx_period"] = self.adx_period
        params["adx_min"] = self.adx_min
        return params


class DonchianTrendFilterADX20(DonchianTrendFilterADX):
    """Thin wrapper: DonchianTrendFilterADX pinned at adx_min=20."""

    name: str = "Donchian_TrendFilter_ADX20"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("adx_min", 20.0)
        super().__init__(**kwargs)


class DonchianTrendFilterADX25(DonchianTrendFilterADX):
    """Thin wrapper: DonchianTrendFilterADX pinned at adx_min=25."""

    name: str = "Donchian_TrendFilter_ADX25"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("adx_min", 25.0)
        super().__init__(**kwargs)


__all__ = [
    "DonchianTrendFilterADX",
    "DonchianTrendFilterADX20",
    "DonchianTrendFilterADX25",
]
