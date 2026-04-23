"""오실레이터 지표 — RSI, MACD. 순수 함수."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from src.core.types import BarSeries


@dataclass(frozen=True)
class RSIResult:
    values: np.ndarray


@dataclass(frozen=True)
class MACDResult:
    macd_line: np.ndarray
    signal_line: np.ndarray
    histogram: np.ndarray


def rsi(series: BarSeries, period: int = 14) -> RSIResult:
    close = series.close
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    result = (100.0 - (100.0 / (1.0 + rs))).to_numpy()
    result[:period] = np.nan
    return RSIResult(values=result)


def macd(series: BarSeries, fast: int = 12, slow: int = 26, signal: int = 9) -> MACDResult:
    close = series.close
    fast_ema = close.ewm(span=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, adjust=False).mean()
    macd_line = (fast_ema - slow_ema).to_numpy()
    macd_line[:slow-1] = np.nan
    signal_line = pd.Series(macd_line).ewm(span=signal, adjust=False).mean().to_numpy()
    signal_line[:slow-1+signal-1] = np.nan
    histogram = macd_line - signal_line
    return MACDResult(macd_line=macd_line, signal_line=signal_line, histogram=histogram)


__all__ = ["rsi", "macd", "RSIResult", "MACDResult"]
