"""RSI (Relative Strength Index) 지표 계산 모듈.

Wilder 스무딩 방식으로 RSI를 계산한다.
RSI ≥ 70: 과매수, RSI ≤ 30: 과매도.
"""

import numpy as np
import pandas as pd


def calc_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """RSI 계산 (Wilder 스무딩).

    워밍업 기간(period + 1 봉) 이전은 NaN을 유지한다.

    Args:
        df: OHLCV DataFrame. 'close' 컬럼 필요.
        period: RSI 기간 (기본 14)

    Returns:
        'rsi' 컬럼이 추가된 DataFrame (복사본).
    """
    df = df.copy()
    close = df["close"].to_numpy(dtype=float)
    n = len(df)

    rsi_arr = np.full(n, np.nan)

    if n < period + 2:
        df["rsi"] = rsi_arr
        return df

    # 일별 변화량
    delta = np.full(n, np.nan)
    for i in range(1, n):
        if not (np.isnan(close[i]) or np.isnan(close[i - 1])):
            delta[i] = close[i] - close[i - 1]

    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    # 초기 평균 이득/손실 (단순 평균, period 기간)
    # 첫 번째 유효 delta 위치 찾기
    first_valid = next((i for i in range(1, n) if not np.isnan(delta[i])), -1)
    if first_valid == -1:
        df["rsi"] = rsi_arr
        return df

    init_end = first_valid + period
    if init_end > n:
        df["rsi"] = rsi_arr
        return df

    avg_gain = np.nanmean(gain[first_valid:init_end])
    avg_loss = np.nanmean(loss[first_valid:init_end])

    # 첫 RSI 값
    if avg_loss == 0:
        rsi_arr[init_end - 1] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_arr[init_end - 1] = 100.0 - (100.0 / (1.0 + rs))

    # Wilder 스무딩
    for i in range(init_end, n):
        g = gain[i] if not np.isnan(gain[i]) else 0.0
        l = loss[i] if not np.isnan(loss[i]) else 0.0

        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

        if avg_loss == 0:
            rsi_arr[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_arr[i] = 100.0 - (100.0 / (1.0 + rs))

    df["rsi"] = rsi_arr
    return df


__all__ = ["calc_rsi"]
