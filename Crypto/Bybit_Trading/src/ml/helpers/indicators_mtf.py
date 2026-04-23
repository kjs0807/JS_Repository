"""ML-side thin wrappers around src/strategies/indicators.

These return plain np.ndarray (not the strategy-side Result dataclasses)
so pattern code can reference indicator values directly without unwrapping
.values every call.
"""
from __future__ import annotations

import numpy as np

from src.core.types import BarSeries
from src.strategies.indicators.momentum import atr as _atr
from src.strategies.indicators.momentum import adx as _adx
from src.strategies.indicators.momentum import bollinger as _bollinger
from src.strategies.indicators.oscillator import rsi as _rsi
from src.strategies.indicators.trend import ema as _ema


def compute_rsi(series: BarSeries, period: int = 14) -> np.ndarray:
    """Wilder RSI as a numpy array aligned to the BarSeries DataFrame index."""
    return _rsi(series, period=period).values


def compute_atr(series: BarSeries, period: int = 14) -> np.ndarray:
    """Wilder ATR as a numpy array aligned to the BarSeries DataFrame index."""
    return _atr(series, period=period).values


def compute_ema(series: BarSeries, period: int = 20) -> np.ndarray:
    """EMA close as a numpy array aligned to the BarSeries DataFrame index."""
    return _ema(series, period=period).values


def compute_adx(
    series: BarSeries, period: int = 14
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (adx, +DI, -DI) arrays aligned to the BarSeries index."""
    result = _adx(series, period=period)
    return result.values, result.plus_di, result.minus_di


def compute_bb_width(
    series: BarSeries, period: int = 20, std: float = 2.0
) -> np.ndarray:
    """Bollinger band width (bandwidth = (upper - lower) / mid) as np.ndarray."""
    return _bollinger(series, period=period, std=std).bandwidth


def compute_percentile_rank(
    arr: np.ndarray,
    lookback: int,
    min_lookback: int | None = None,
) -> np.ndarray:
    """Rolling percentile rank of arr within the trailing `lookback` window.

    For each index i, returns (# samples <= arr[i]) / (# valid samples) inside
    arr[max(0, i - lookback + 1) : i + 1]. NaN inputs propagate to NaN outputs.
    If `min_lookback` is given and the window has fewer than that many valid
    samples, the output at i is NaN.
    """
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    n = len(arr)
    out = np.full(n, np.nan, dtype=float)
    for i in range(n):
        xi = arr[i]
        if np.isnan(xi):
            continue
        lo = max(0, i - lookback + 1)
        window = arr[lo : i + 1]
        valid_mask = ~np.isnan(window)
        valid = window[valid_mask]
        if len(valid) == 0:
            continue
        if min_lookback is not None and len(valid) < min_lookback:
            continue
        rank = float(np.sum(valid <= xi)) / float(len(valid))
        out[i] = rank
    return out


__all__ = [
    "compute_rsi",
    "compute_atr",
    "compute_ema",
    "compute_adx",
    "compute_bb_width",
    "compute_percentile_rank",
]
