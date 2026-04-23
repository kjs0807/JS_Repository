"""Donchian Breakout — Variant B (TrendFilter).
20일 고/저가 돌파 + EMA 추세 필터 + ATR 스톱.
"""
from __future__ import annotations
from typing import Optional
import numpy as np
from src.core.types import Bar, BarSeries
from src.execution.broker import Broker, Fill
from src.strategies.indicators.channel import donchian
from src.strategies.indicators.trend import ema
from src.strategies.indicators.momentum import atr

class DonchianTrendFilter:
    name: str = "Donchian_TrendFilter"

    def __init__(self, entry_period=20, exit_period=10, ema_filter=200,
                 atr_period=14, stop_atr=2.0, timeframe="1h"):
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.ema_filter = ema_filter
        self.atr_period = atr_period
        self.stop_atr = stop_atr
        self.timeframe = timeframe

    @property
    def warmup_bars(self) -> int:
        return max(self.entry_period, self.exit_period, self.ema_filter, self.atr_period) + 10

    def on_bar(self, bar: Bar, series: BarSeries, broker: Broker) -> None:
        if len(series) < self.warmup_bars:
            return
        ch_entry = donchian(series, period=self.entry_period)
        ch_exit = donchian(series, period=self.exit_period)
        ema_r = ema(series, period=self.ema_filter)
        atr_r = atr(series, period=self.atr_period)

        upper_entry = ch_entry.upper[-1]
        lower_entry = ch_entry.lower[-1]
        upper_exit = ch_exit.upper[-1]
        lower_exit = ch_exit.lower[-1]
        ema_val = ema_r.values[-1]
        atr_val = atr_r.values[-1]

        if any(np.isnan(x) for x in [upper_entry, lower_entry, upper_exit, lower_exit, ema_val, atr_val]):
            return
        if atr_val <= 0:
            return

        close = bar.close
        pos = broker.get_position(bar.symbol)

        if pos is not None:
            if pos.side == "LONG":
                if close < lower_exit or close < ema_val:
                    broker.close(bar.symbol, reason=f"exit{self.exit_period} or ema break")
            elif pos.side == "SHORT":
                if close > upper_exit or close > ema_val:
                    broker.close(bar.symbol, reason=f"exit{self.exit_period} or ema break")
            return

        if close > upper_entry and close > ema_val:
            sl = close - self.stop_atr * atr_val
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=close - sl)
            if qty > 0:
                broker.buy(bar.symbol, qty, stop_loss=sl,
                          reason=f"Donchian {self.entry_period} LONG")
        elif close < lower_entry and close < ema_val:
            sl = close + self.stop_atr * atr_val
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=sl - close)
            if qty > 0:
                broker.sell(bar.symbol, qty, stop_loss=sl,
                           reason=f"Donchian {self.entry_period} SHORT")

    def prepare(self, full_series: BarSeries):
        """전체 시계열에 대해 지표를 사전 계산한다."""
        from src.strategies.base import IndicatorCache
        ch_entry = donchian(full_series, period=self.entry_period)
        ch_exit = donchian(full_series, period=self.exit_period)
        ema_r = ema(full_series, period=self.ema_filter)
        atr_r = atr(full_series, period=self.atr_period)
        return IndicatorCache(arrays={
            "upper_entry": ch_entry.upper,
            "lower_entry": ch_entry.lower,
            "upper_exit": ch_exit.upper,
            "lower_exit": ch_exit.lower,
            "ema": ema_r.values,
            "atr": atr_r.values,
        })

    def on_bar_fast(self, bar: Bar, i: int, cache, broker) -> None:
        """사전 계산된 cache에서 인덱스로 조회."""
        upper_entry = cache.arrays["upper_entry"][i]
        lower_entry = cache.arrays["lower_entry"][i]
        upper_exit = cache.arrays["upper_exit"][i]
        lower_exit = cache.arrays["lower_exit"][i]
        ema_val = cache.arrays["ema"][i]
        atr_val = cache.arrays["atr"][i]

        if any(np.isnan(x) for x in [upper_entry, lower_entry, upper_exit, lower_exit, ema_val, atr_val]):
            return
        if atr_val <= 0:
            return

        close = bar.close
        pos = broker.get_position(bar.symbol)

        if pos is not None:
            if pos.side == "LONG":
                if close < lower_exit or close < ema_val:
                    broker.close(bar.symbol, reason=f"exit{self.exit_period} or ema break")
            elif pos.side == "SHORT":
                if close > upper_exit or close > ema_val:
                    broker.close(bar.symbol, reason=f"exit{self.exit_period} or ema break")
            return

        if close > upper_entry and close > ema_val:
            sl = close - self.stop_atr * atr_val
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=close - sl)
            if qty > 0:
                broker.buy(bar.symbol, qty, stop_loss=sl,
                          reason=f"Donchian {self.entry_period} LONG")
        elif close < lower_entry and close < ema_val:
            sl = close + self.stop_atr * atr_val
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=sl - close)
            if qty > 0:
                broker.sell(bar.symbol, qty, stop_loss=sl,
                           reason=f"Donchian {self.entry_period} SHORT")

    def on_fill(self, fill: Fill) -> None:
        pass

    def get_params(self) -> dict:
        return {"entry_period": self.entry_period, "exit_period": self.exit_period,
                "ema_filter": self.ema_filter, "atr_period": self.atr_period,
                "stop_atr": self.stop_atr}

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

__all__ = ["DonchianTrendFilter"]
