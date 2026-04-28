"""KAMA (Kaufman Adaptive Moving Average) 지표 계산 모듈.

Efficiency Ratio(ER)로 평활 계수를 동적으로 조정한다.
추세 강할수록 빠른 반응, 노이즈 클수록 느린 반응.
Signal_Trading/src/indicators/kama.py 기반 재구현 (DataFrame 인터페이스로 확장).
"""

import numpy as np
import pandas as pd


def calc_kama(
    df: pd.DataFrame,
    period: int = 10,
    fast: int = 2,
    slow: int = 30,
) -> pd.DataFrame:
    """KAMA 및 Efficiency Ratio 계산.

    ER = |close[t] - close[t-period]| / Σ|close[i] - close[i-1]| (period 합산)
    SC = (ER × (fast_sc - slow_sc) + slow_sc)²
    KAMA[t] = KAMA[t-1] + SC × (close[t] - KAMA[t-1])

    fast_sc = 2 / (fast + 1), slow_sc = 2 / (slow + 1)

    워밍업 기간(period 봉) 이전은 NaN을 유지한다.

    Args:
        df: OHLCV DataFrame. 'close' 컬럼 필요.
        period: Efficiency Ratio 계산 기간 (기본 10)
        fast: 빠른 EMA 평활 기간 (기본 2)
        slow: 느린 EMA 평활 기간 (기본 30)

    Returns:
        'kama', 'efficiency_ratio' 컬럼이 추가된 DataFrame (복사본).
    """
    df = df.copy()
    close_arr = df["close"].to_numpy(dtype=float)
    n = len(close_arr)

    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)

    kama_arr = np.full(n, np.nan)
    er_arr = np.full(n, np.nan)

    # 첫 번째 유효 인덱스 탐색
    first_valid = next(
        (i for i in range(n) if not np.isnan(close_arr[i])), -1
    )
    if first_valid == -1:
        df["kama"] = kama_arr
        df["efficiency_ratio"] = er_arr
        return df

    kama_start = first_valid + period
    if kama_start >= n:
        df["kama"] = kama_arr
        df["efficiency_ratio"] = er_arr
        return df

    # KAMA 초기값: kama_start 시점 종가
    kama_prev = close_arr[kama_start]
    kama_arr[kama_start] = kama_prev

    for i in range(kama_start + 1, n):
        c_i = close_arr[i]
        if np.isnan(c_i):
            # NaN 위치는 직전 KAMA 값 유지
            kama_arr[i] = kama_prev
            continue

        c_n = close_arr[i - period]
        if np.isnan(c_n):
            kama_arr[i] = kama_prev
            continue

        # Efficiency Ratio
        direction = abs(c_i - c_n)
        window = close_arr[i - period + 1: i + 1]
        volatility = float(np.nansum(np.abs(np.diff(window))))

        if volatility == 0.0:
            er = 0.0
        else:
            er = direction / volatility

        er_arr[i] = er

        # Smoothing Constant
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2

        # KAMA 업데이트
        kama_val = kama_prev + sc * (c_i - kama_prev)
        kama_arr[i] = kama_val
        kama_prev = kama_val

    df["kama"] = kama_arr
    df["efficiency_ratio"] = er_arr
    return df


__all__ = ["calc_kama"]
