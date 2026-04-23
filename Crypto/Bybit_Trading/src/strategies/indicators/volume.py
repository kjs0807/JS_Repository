"""거래량 지표 — VWAP, Volume Price Divergence."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.core.types import BarSeries


@dataclass(frozen=True)
class VWAPResult:
    """VWAP 결과."""
    values: np.ndarray


@dataclass(frozen=True)
class VolumeDivergenceResult:
    """거래량-가격 다이버전스 결과."""
    divergence: np.ndarray  # -1 (약세 다이버전스), 0 (없음), +1 (강세 다이버전스)


def vwap(series: BarSeries, period: int = 20) -> VWAPResult:
    """롤링 VWAP (Volume Weighted Average Price).

    VWAP[i] = sum(typical_price * volume) / sum(volume) over period
    typical_price = (high + low + close) / 3
    """
    high = series.high
    low = series.low
    close = series.close
    volume = series.volume
    typical_price = (high + low + close) / 3.0
    pv = typical_price * volume

    pv_sum = pv.rolling(window=period, min_periods=period).sum()
    v_sum = volume.rolling(window=period, min_periods=period).sum()
    vwap_values = pv_sum / (v_sum + 1e-10)
    return VWAPResult(values=vwap_values.to_numpy())


def volume_price_divergence(
    series: BarSeries, period: int = 20
) -> VolumeDivergenceResult:
    """거래량-가격 다이버전스 감지.

    로직:
    - 가격이 period 동안 새 고점을 찍었는데 거래량이 감소 → 약세 다이버전스 (-1)
    - 가격이 period 동안 새 저점을 찍었는데 거래량이 감소 → 강세 다이버전스 (+1)
    - 그 외 → 0
    """
    high = series.high.to_numpy()
    low = series.low.to_numpy()
    vol = series.volume.to_numpy()
    n = len(high)
    divergence = np.zeros(n)

    for i in range(period, n):
        recent_high = np.max(high[i-period:i])
        recent_low = np.min(low[i-period:i])
        recent_vol = np.mean(vol[i-period:i])

        if high[i] > recent_high and vol[i] < recent_vol:
            divergence[i] = -1.0  # 약세
        elif low[i] < recent_low and vol[i] < recent_vol:
            divergence[i] = 1.0   # 강세

    return VolumeDivergenceResult(divergence=divergence)


__all__ = ["vwap", "volume_price_divergence", "VWAPResult", "VolumeDivergenceResult"]
