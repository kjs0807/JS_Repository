"""모멘텀/변동성 지표 — ATR, ADX, Bollinger Bands, Keltner Channel. 순수 함수."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from src.core.types import BarSeries


@dataclass(frozen=True)
class ATRResult:
    values: np.ndarray


@dataclass(frozen=True)
class ADXResult:
    values: np.ndarray
    plus_di: np.ndarray
    minus_di: np.ndarray


@dataclass(frozen=True)
class BollingerResult:
    upper: np.ndarray
    mid: np.ndarray
    lower: np.ndarray
    bandwidth: np.ndarray


@dataclass(frozen=True)
class KeltnerResult:
    upper: np.ndarray
    mid: np.ndarray
    lower: np.ndarray


def atr(series: BarSeries, period: int = 14) -> ATRResult:
    high, low, close = series.high, series.low, series.close
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    result = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean().to_numpy()
    result[:period] = np.nan
    return ATRResult(values=result)


def adx(series: BarSeries, period: int = 14) -> ADXResult:
    high, low, close = series.high, series.low, series.close
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)
    smoothed_tr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    smoothed_plus = plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    smoothed_minus = minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100.0 * smoothed_plus / (smoothed_tr + 1e-10)
    minus_di = 100.0 * smoothed_minus / (smoothed_tr + 1e-10)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx_values = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean().to_numpy()
    adx_values[:period * 2] = np.nan
    return ADXResult(values=adx_values, plus_di=plus_di.to_numpy(), minus_di=minus_di.to_numpy())


def bollinger(series: BarSeries, period: int = 20, std: float = 2.0) -> BollingerResult:
    close = series.close
    mid = close.rolling(window=period).mean()
    rolling_std = close.rolling(window=period).std()
    upper = mid + std * rolling_std
    lower = mid - std * rolling_std
    bandwidth = ((upper - lower) / (mid + 1e-10)).to_numpy()
    return BollingerResult(
        upper=upper.to_numpy(),
        mid=mid.to_numpy(),
        lower=lower.to_numpy(),
        bandwidth=bandwidth,
    )


def keltner(series: BarSeries, ema_period: int = 20, atr_period: int = 14,
            atr_mult: float = 1.5) -> KeltnerResult:
    close = series.close
    mid = close.ewm(span=ema_period, adjust=False).mean()
    atr_vals = pd.Series(atr(series, period=atr_period).values, index=close.index)
    upper = mid + atr_mult * atr_vals
    lower = mid - atr_mult * atr_vals
    return KeltnerResult(upper=upper.to_numpy(), mid=mid.to_numpy(), lower=lower.to_numpy())


__all__ = ["atr", "adx", "bollinger", "keltner", "ATRResult", "ADXResult", "BollingerResult", "KeltnerResult"]
