"""ATR (Average True Range) 지표 계산 모듈.

Wilder 스무딩 방식으로 ATR을 계산한다.
True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
"""

import numpy as np
import pandas as pd


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """ATR (Average True Range) 계산.

    Wilder 스무딩(RMA) 방식을 사용한다.
    워밍업 기간(period 봉) 이전은 NaN을 유지한다.

    Args:
        df: OHLCV DataFrame. 최소 'high', 'low', 'close' 컬럼 필요.
        period: ATR 계산 기간 (기본 14)

    Returns:
        'atr' 컬럼이 추가된 DataFrame (원본 수정 없이 복사본 반환).

    Note:
        - NaN이 포함된 봉은 TR 계산에서 NaN 처리됨
        - 첫 번째 ATR 값은 단순 평균 (SMA) 기반으로 초기화
    """
    df = df.copy()
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = len(df)

    # True Range 계산
    tr = np.full(n, np.nan)
    for i in range(1, n):
        if np.isnan(high[i]) or np.isnan(low[i]) or np.isnan(close[i - 1]):
            continue
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

    # Wilder 스무딩 (RMA): ATR[t] = (ATR[t-1] * (n-1) + TR[t]) / n
    atr = np.full(n, np.nan)
    # 첫 ATR 값: period 번째 봉부터 단순 평균으로 초기화
    first_valid = -1
    for i in range(1, n):
        if not np.isnan(tr[i]):
            first_valid = i
            break

    if first_valid == -1 or first_valid + period - 1 >= n:
        df["atr"] = np.nan
        return df

    start = first_valid
    end = start + period
    if end > n:
        df["atr"] = np.nan
        return df

    # 초기 ATR: 첫 period개 TR의 평균
    init_slice = tr[start:end]
    if np.any(np.isnan(init_slice)):
        df["atr"] = np.nan
        return df

    atr[end - 1] = np.mean(init_slice)

    # Wilder 스무딩 적용
    for i in range(end, n):
        if np.isnan(tr[i]):
            atr[i] = atr[i - 1]  # TR NaN이면 직전 ATR 유지
        else:
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    df["atr"] = atr
    return df


__all__ = ["calc_atr"]
