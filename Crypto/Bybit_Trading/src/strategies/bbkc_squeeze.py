"""BBKCSqueeze — Bollinger Band / Keltner Channel Squeeze Breakout (Variant: fixed TP/SL).

Strategy 2 of 9. Breakout category (volatility expansion).
"""
from __future__ import annotations
from typing import Optional
import numpy as np

from src.core.types import Bar, BarSeries
from src.execution.broker import Broker, Fill
from src.strategies.base import IndicatorCache
from src.strategies.indicators.momentum import bollinger, keltner, atr
from src.strategies.indicators.oscillator import rsi


class BBKCSqueeze:
    """BB/KC Squeeze breakout with RSI filter and fixed % TP/SL."""

    name: str = "BBKCSqueeze"

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 1.5,
        kc_period: int = 20,
        kc_mult: float = 1.0,
        atr_period: int = 14,
        rsi_period: int = 14,
        rsi_filter: float = 70.0,
        tp_pct: float = 0.06,
        sl_pct: float = 0.07,
        leverage: int = 3,
        timeframe: str = "1h",
    ) -> None:
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.kc_period = kc_period
        self.kc_mult = kc_mult
        self.atr_period = atr_period
        self.rsi_period = rsi_period
        self.rsi_filter = rsi_filter
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.leverage = leverage
        self.timeframe = timeframe

    @property
    def warmup_bars(self) -> int:
        return max(self.bb_period, self.kc_period, self.atr_period, self.rsi_period) + 10

    def prepare(self, full_series: BarSeries) -> IndicatorCache:
        """전체 시계열에 대해 지표를 사전 계산한다."""
        bb = bollinger(full_series, period=self.bb_period, std=self.bb_std)
        kc = keltner(full_series, ema_period=self.kc_period,
                     atr_period=self.atr_period, atr_mult=self.kc_mult)
        rsi_r = rsi(full_series, period=self.rsi_period)
        # squeeze_on[i] = 1 if BB is inside KC at bar i, else 0
        squeeze_on = ((bb.upper < kc.upper) & (bb.lower > kc.lower)).astype(float)
        return IndicatorCache(arrays={
            "bb_upper": bb.upper,
            "bb_mid": bb.mid,
            "bb_lower": bb.lower,
            "kc_upper": kc.upper,
            "kc_lower": kc.lower,
            "rsi": rsi_r.values,
            "squeeze_on": squeeze_on,
        })

    def on_bar_fast(self, bar: Bar, i: int, cache: IndicatorCache, broker: Broker) -> None:
        """사전 계산된 cache에서 인덱스로 조회."""
        if i < 1:
            return

        # 지표 값 조회
        bb_mid = cache.arrays["bb_mid"][i]
        rsi_val = cache.arrays["rsi"][i]
        squeeze_now = cache.arrays["squeeze_on"][i]
        squeeze_prev = cache.arrays["squeeze_on"][i - 1]

        # NaN 체크
        if np.isnan(bb_mid) or np.isnan(rsi_val) or np.isnan(squeeze_now) or np.isnan(squeeze_prev):
            return

        close = bar.close
        pos = broker.get_position(bar.symbol)

        # 이미 포지션 있으면 스킵 (Fixed TP/SL은 Broker가 자동 처리)
        if pos is not None:
            return

        # Squeeze 해제 감지: 직전 봉 squeeze ON → 현재 봉 squeeze OFF
        if not (squeeze_prev >= 1.0 and squeeze_now < 1.0):
            return

        price_tp = self.tp_pct / self.leverage
        price_sl = self.sl_pct / self.leverage

        # LONG: 상단 이탈 + RSI 과열 아님
        if close > bb_mid and rsi_val < self.rsi_filter:
            tp = close * (1 + price_tp)
            sl = close * (1 - price_sl)
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=close - sl)
            if qty > 0:
                broker.buy(bar.symbol, qty, stop_loss=sl, take_profit=tp,
                           reason=f"BBKCSqueeze LONG rsi={rsi_val:.1f}")

        # SHORT: 하단 이탈 + RSI 과매도 아님
        elif close < bb_mid and rsi_val > (100.0 - self.rsi_filter):
            tp = close * (1 - price_tp)
            sl = close * (1 + price_sl)
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=sl - close)
            if qty > 0:
                broker.sell(bar.symbol, qty, stop_loss=sl, take_profit=tp,
                            reason=f"BBKCSqueeze SHORT rsi={rsi_val:.1f}")

    def on_bar(self, bar: Bar, series: BarSeries, broker: Broker) -> None:
        """Legacy 경로 — prepare/on_bar_fast가 있으므로 엔진이 이걸 호출 안 함.
        단위 테스트 호환을 위해 on_bar_fast와 동일한 결과 생성."""
        if len(series) < self.warmup_bars:
            return
        # 전체 시리즈에 대해 지표 계산 후 마지막 값 사용
        cache = self.prepare(series)
        idx = len(series) - 1
        self.on_bar_fast(bar, idx, cache, broker)

    def on_fill(self, fill: Fill) -> None:
        pass

    def get_params(self) -> dict:
        return {
            "bb_period": self.bb_period,
            "bb_std": self.bb_std,
            "kc_period": self.kc_period,
            "kc_mult": self.kc_mult,
            "atr_period": self.atr_period,
            "rsi_period": self.rsi_period,
            "rsi_filter": self.rsi_filter,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "leverage": self.leverage,
        }

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)


__all__ = ["BBKCSqueeze"]
