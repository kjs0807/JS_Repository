"""DataFeed — 백테스트/실거래 데이터 공급 인터페이스."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Protocol, runtime_checkable

import pandas as pd

from src.core.types import Bar, BarSeries
from src.data_manager.db import DBManager

logger = logging.getLogger(__name__)


@runtime_checkable
class DataFeed(Protocol):
    def next_bar(self, symbol: str) -> Optional[Bar]: ...
    def get_history(self, symbol: str, lookback: int) -> BarSeries: ...
    def has_next(self) -> bool: ...
    @property
    def bar_count(self) -> int: ...


class HistoricalDataFeed:
    """DB 기반 과거 데이터 순차 공급 (백테스트용)."""

    def __init__(self, db: DBManager, symbols: List[str], timeframe: str,
                 start_time: Optional[int] = None, end_time: Optional[int] = None) -> None:
        self.db = db
        self.symbols = symbols
        self.timeframe = timeframe
        self._data: Dict[str, pd.DataFrame] = {}
        self._indices: Dict[str, int] = {}
        self._bar_count: int = 0
        for symbol in symbols:
            df = db.get_bars(symbol, timeframe, start_time, end_time)
            self._data[symbol] = df
            self._indices[symbol] = 0

    def next_bar(self, symbol: str) -> Optional[Bar]:
        df = self._data.get(symbol)
        if df is None or df.empty:
            return None
        idx = self._indices.get(symbol, 0)
        if idx >= len(df):
            return None
        row = df.iloc[idx]
        self._indices[symbol] = idx + 1
        self._bar_count += 1
        turnover_val = row.get("turnover") if hasattr(row, "get") else row["turnover"] if "turnover" in df.columns else None
        turnover = float(turnover_val) if turnover_val is not None and pd.notna(turnover_val) else None
        return Bar(
            symbol=symbol, timestamp=int(row["open_time"]),
            timeframe=self.timeframe,
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row["volume"]),
            turnover=turnover,
        )

    def get_history(self, symbol: str, lookback: int) -> BarSeries:
        df = self._data.get(symbol)
        if df is None or df.empty:
            return BarSeries(symbol=symbol, timeframe=self.timeframe, bars=pd.DataFrame())
        idx = self._indices.get(symbol, 0)
        start = max(0, idx - lookback)
        slice_df = df.iloc[start:idx].copy()
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in slice_df.columns]
        return BarSeries(
            symbol=symbol, timeframe=self.timeframe,
            bars=slice_df[cols].reset_index(drop=True),
        )

    def get_full_series(self, symbol: str) -> BarSeries:
        """전체 심볼 데이터를 BarSeries로 반환.

        인덱스 위치와 무관하게 모든 봉을 반환한다.
        전략의 prepare()에서 사전 계산용으로 사용.

        Includes a ``timestamp`` column (ms since epoch) when the
        underlying DB DataFrame has ``open_time``. HTF-aware strategies
        like BBKCSqueezeHTFTrend need actual timestamps to bucket 1h
        bars into 4h groups; OHLCV-only strategies ignore the extra
        column so this is backward-compatible.
        """
        df = self._data.get(symbol)
        if df is None or df.empty:
            return BarSeries(symbol=symbol, timeframe=self.timeframe, bars=pd.DataFrame())
        cols = ["open", "high", "low", "close", "volume"]
        out = df[cols].reset_index(drop=True)
        if "open_time" in df.columns:
            out.insert(
                0,
                "timestamp",
                df["open_time"].reset_index(drop=True).astype("int64"),
            )
        return BarSeries(
            symbol=symbol,
            timeframe=self.timeframe,
            bars=out,
        )

    def has_next(self) -> bool:
        return any(self._indices.get(s, 0) < len(df) for s, df in self._data.items())

    @property
    def bar_count(self) -> int:
        return self._bar_count

    def reset(self) -> None:
        for symbol in self._indices:
            self._indices[symbol] = 0
        self._bar_count = 0


__all__ = ["DataFeed", "HistoricalDataFeed"]
