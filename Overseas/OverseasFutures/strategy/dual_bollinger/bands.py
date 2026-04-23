"""Dual Bollinger Band Breakout Strategy — Band Calculation."""

from typing import Dict
import pandas as pd
import numpy as np

from strategy.dual_bollinger.config import DualBBConfig


def calculate_bands(closes: pd.Series, config: DualBBConfig) -> Dict[str, pd.Series]:
    """Calculate Bollinger Bands for inner and outer band breakout strategy.

    Args:
        closes: Series of closing prices.
        config: Strategy configuration.

    Returns:
        Dictionary with keys: 'ma', 'std', 'inner_upper', 'inner_lower',
        'outer_upper', 'outer_lower'.
    """
    ma = closes.ewm(span=config.ma_period, adjust=False).mean()
    std = closes.ewm(span=config.ma_period, adjust=False).std()

    return {
        'ma': ma,
        'std': std,
        'inner_upper': ma + config.sigma_inner * std,
        'inner_lower': ma - config.sigma_inner * std,
        'outer_upper': ma + config.sigma_outer * std,
        'outer_lower': ma - config.sigma_outer * std,
    }


def calculate_atr(highs: pd.Series, lows: pd.Series, closes: pd.Series,
                   period: int = 14) -> pd.Series:
    """Calculate Average True Range (ATR).

    Args:
        highs: Series of high prices.
        lows: Series of low prices.
        closes: Series of closing prices.
        period: ATR lookback period.

    Returns:
        Series of ATR values. First `period` values will be NaN.
    """
    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_close).abs(),
        (lows - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calculate_bandwidth(inner_upper: pd.Series, inner_lower: pd.Series,
                        ma: pd.Series) -> pd.Series:
    """Calculate Bollinger Bandwidth as percentage of MA.

    Args:
        inner_upper: Inner upper band series.
        inner_lower: Inner lower band series.
        ma: Moving average series.

    Returns:
        Series of bandwidth percentage values.
    """
    return ((inner_upper - inner_lower) / ma) * 100


def calculate_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI using Wilder's smoothing method.

    Args:
        closes: Series of closing prices.
        period: RSI lookback period.

    Returns:
        Series of RSI values (0-100). First `period` values will be NaN.
    """
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    # Wilder's smoothing: first value is SMA, then exponential
    avg_gain = np.full(len(closes), np.nan)
    avg_loss = np.full(len(closes), np.nan)

    # 초기 SMA
    if len(closes) > period:
        avg_gain[period] = gain.iloc[1:period + 1].mean()
        avg_loss[period] = loss.iloc[1:period + 1].mean()

        # Wilder's smoothing
        for i in range(period + 1, len(closes)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain.iloc[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss.iloc[i]) / period

    avg_gain_s = pd.Series(avg_gain, index=closes.index)
    avg_loss_s = pd.Series(avg_loss, index=closes.index)

    rs = avg_gain_s / avg_loss_s
    rsi = 100 - (100 / (1 + rs))

    return rsi
