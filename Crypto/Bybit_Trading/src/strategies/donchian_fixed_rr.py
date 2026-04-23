"""Donchian Breakout — Variant C (FixedRR).
N일 돌파 + 고정 R/R TP + ATR 트레일링 스톱.
"""
from __future__ import annotations
from typing import Optional
import numpy as np
from src.core.types import Bar, BarSeries
from src.execution.broker import Broker, Fill
from src.strategies.indicators.channel import donchian
from src.strategies.indicators.momentum import atr

class DonchianFixedRR:
    name: str = "Donchian_FixedRR"

    def __init__(self, entry_period=20, atr_period=14, stop_atr=2.5, tp_r_ratio=2.0,
                 trail_activate_atr=1.5, trail_distance_atr=1.0, timeframe="1h"):
        self.entry_period = entry_period
        self.atr_period = atr_period
        self.stop_atr = stop_atr
        self.tp_r_ratio = tp_r_ratio
        self.trail_activate_atr = trail_activate_atr
        self.trail_distance_atr = trail_distance_atr
        self.timeframe = timeframe

    @property
    def warmup_bars(self) -> int:
        return max(self.entry_period, self.atr_period) + 1

    def on_bar(self, bar: Bar, series: BarSeries, broker: Broker) -> None:
        if len(series) < self.warmup_bars:
            return
        ch = donchian(series, period=self.entry_period)
        atr_r = atr(series, period=self.atr_period)
        upper = ch.upper[-1]
        lower = ch.lower[-1]
        atr_val = atr_r.values[-1]
        if any(np.isnan(x) for x in [upper, lower, atr_val]):
            return
        if atr_val <= 0:
            return

        close = bar.close
        pos = broker.get_position(bar.symbol)

        if pos is not None:
            self._update_trailing(pos, close, atr_val, broker)
            return

        if close > upper:
            sl = close - self.stop_atr * atr_val
            tp = close + (self.stop_atr * self.tp_r_ratio) * atr_val
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=close - sl)
            if qty > 0:
                broker.buy(bar.symbol, qty, stop_loss=sl, take_profit=tp,
                          reason=f"Donchian {self.entry_period} LONG RR1:{self.tp_r_ratio}")
        elif close < lower:
            sl = close + self.stop_atr * atr_val
            tp = close - (self.stop_atr * self.tp_r_ratio) * atr_val
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=sl - close)
            if qty > 0:
                broker.sell(bar.symbol, qty, stop_loss=sl, take_profit=tp,
                           reason=f"Donchian {self.entry_period} SHORT RR1:{self.tp_r_ratio}")

    def prepare(self, full_series: BarSeries):
        """전체 시계열에 대해 지표를 사전 계산한다."""
        from src.strategies.base import IndicatorCache
        ch = donchian(full_series, period=self.entry_period)
        atr_r = atr(full_series, period=self.atr_period)
        return IndicatorCache(arrays={
            "upper": ch.upper,
            "lower": ch.lower,
            "atr": atr_r.values,
        })

    def on_bar_fast(self, bar: Bar, i: int, cache, broker) -> None:
        """사전 계산된 cache에서 인덱스로 조회."""
        upper = cache.arrays["upper"][i]
        lower = cache.arrays["lower"][i]
        atr_val = cache.arrays["atr"][i]

        if any(np.isnan(x) for x in [upper, lower, atr_val]):
            return
        if atr_val <= 0:
            return

        close = bar.close
        pos = broker.get_position(bar.symbol)

        if pos is not None:
            self._update_trailing(pos, close, atr_val, broker)
            return

        if close > upper:
            sl = close - self.stop_atr * atr_val
            tp = close + (self.stop_atr * self.tp_r_ratio) * atr_val
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=close - sl)
            if qty > 0:
                broker.buy(bar.symbol, qty, stop_loss=sl, take_profit=tp,
                          reason=f"Donchian {self.entry_period} LONG RR1:{self.tp_r_ratio}")
        elif close < lower:
            sl = close + self.stop_atr * atr_val
            tp = close - (self.stop_atr * self.tp_r_ratio) * atr_val
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=sl - close)
            if qty > 0:
                broker.sell(bar.symbol, qty, stop_loss=sl, take_profit=tp,
                           reason=f"Donchian {self.entry_period} SHORT RR1:{self.tp_r_ratio}")

    def _update_trailing(self, pos, current_price, atr_val, broker):
        activate_distance = self.trail_activate_atr * atr_val
        trail_distance = self.trail_distance_atr * atr_val
        if pos.side == "LONG":
            profit = current_price - pos.entry_price
            if profit >= activate_distance:
                new_stop = current_price - trail_distance
                if new_stop > pos.stop_loss:
                    broker.update_stop(pos.symbol, new_stop)
        elif pos.side == "SHORT":
            profit = pos.entry_price - current_price
            if profit >= activate_distance:
                new_stop = current_price + trail_distance
                if new_stop < pos.stop_loss:
                    broker.update_stop(pos.symbol, new_stop)

    def on_fill(self, fill: Fill) -> None:
        pass

    def get_params(self) -> dict:
        return {"entry_period": self.entry_period, "atr_period": self.atr_period,
                "stop_atr": self.stop_atr, "tp_r_ratio": self.tp_r_ratio,
                "trail_activate_atr": self.trail_activate_atr,
                "trail_distance_atr": self.trail_distance_atr}

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

__all__ = ["DonchianFixedRR"]
