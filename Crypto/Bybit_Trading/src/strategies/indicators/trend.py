"""추세 지표 — EMA, SMA. 순수 함수."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from src.core.types import BarSeries


@dataclass(frozen=True)
class SMAResult:
    values: np.ndarray


@dataclass(frozen=True)
class EMAResult:
    values: np.ndarray


def sma(series: BarSeries, period: int = 20) -> SMAResult:
    values = series.close.rolling(window=period).mean().to_numpy()
    return SMAResult(values=values)


def ema(series: BarSeries, period: int = 20) -> EMAResult:
    ema_series = series.close.ewm(span=period, adjust=False).mean()
    values = ema_series.to_numpy()
    values[:period - 1] = np.nan
    return EMAResult(values=values)


__all__ = ["sma", "ema", "SMAResult", "EMAResult"]
