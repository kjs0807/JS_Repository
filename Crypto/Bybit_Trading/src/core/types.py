"""공통 데이터 타입 정의.

모든 레이어가 공유하는 불변(frozen) 데이터 객체.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class Bar:
    """단일 OHLCV 봉."""
    symbol: str
    timestamp: int
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: Optional[float] = None


@dataclass
class BarSeries:
    """전략에 전달되는 봉 시계열. DataFrame 래퍼."""
    symbol: str
    timeframe: str
    bars: pd.DataFrame

    @property
    def close(self) -> pd.Series:
        return self.bars["close"]

    @property
    def open(self) -> pd.Series:
        return self.bars["open"]

    @property
    def high(self) -> pd.Series:
        return self.bars["high"]

    @property
    def low(self) -> pd.Series:
        return self.bars["low"]

    @property
    def volume(self) -> pd.Series:
        return self.bars["volume"]

    def __len__(self) -> int:
        return len(self.bars)


@dataclass
class ProductInfo:
    """거래소 상품 정보."""
    symbol: str
    base_coin: str
    quote_coin: str = "USDT"
    min_qty: Optional[float] = None
    qty_step: Optional[float] = None
    tick_size: Optional[float] = None
    min_notional: Optional[float] = None
    max_leverage: Optional[int] = None


__all__ = ["Bar", "BarSeries", "ProductInfo"]
