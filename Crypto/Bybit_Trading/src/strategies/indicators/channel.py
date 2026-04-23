"""채널 지표 — Donchian Channel. 순수 함수."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from src.core.types import BarSeries

@dataclass(frozen=True)
class DonchianResult:
    upper: np.ndarray
    lower: np.ndarray
    middle: np.ndarray

def donchian(series: BarSeries, period: int = 20) -> DonchianResult:
    """Donchian Channel. upper[i] = max(high[i-period:i]) — 현재 봉 제외."""
    high = series.high
    low = series.low
    upper_series = high.shift(1).rolling(window=period, min_periods=period).max()
    lower_series = low.shift(1).rolling(window=period, min_periods=period).min()
    upper = upper_series.to_numpy()
    lower = lower_series.to_numpy()
    middle = (upper + lower) / 2.0
    return DonchianResult(upper=upper, lower=lower, middle=middle)

__all__ = ["donchian", "DonchianResult"]
