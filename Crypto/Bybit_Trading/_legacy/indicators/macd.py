"""MACD (Moving Average Convergence Divergence) 지표 계산 모듈.

EMA(fast) - EMA(slow)로 MACD 라인을 계산하고,
MACD의 EMA(signal)로 시그널 라인을 산출한다.
"""

import numpy as np
import pandas as pd


def calc_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD 계산.

    macd_line   = EMA(close, fast) - EMA(close, slow)
    signal_line = EMA(macd_line, signal)
    histogram   = macd_line - signal_line

    워밍업 기간(slow + signal - 1 봉) 이전은 NaN을 유지한다.

    Args:
        df: OHLCV DataFrame. 'close' 컬럼 필요.
        fast: 빠른 EMA 기간 (기본 12)
        slow: 느린 EMA 기간 (기본 26)
        signal: 시그널 EMA 기간 (기본 9)

    Returns:
        'macd_line', 'signal_line', 'histogram' 컬럼이 추가된 DataFrame (복사본).
    """
    df = df.copy()
    close = df["close"]

    # EMA 계산 (pandas ewm, min_periods로 워밍업 NaN 보장)
    ema_fast = close.ewm(span=fast, min_periods=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, min_periods=slow, adjust=False).mean()

    macd_line = ema_fast - ema_slow

    # 시그널 라인: MACD의 EMA
    signal_line = macd_line.ewm(
        span=signal, min_periods=signal, adjust=False
    ).mean()

    histogram = macd_line - signal_line

    df["macd_line"] = macd_line
    df["signal_line"] = signal_line
    df["histogram"] = histogram

    return df


__all__ = ["calc_macd"]
