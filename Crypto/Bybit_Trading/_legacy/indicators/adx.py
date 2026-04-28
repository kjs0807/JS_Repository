"""ADX (Average Directional Index) 지표 계산 모듈.

Wilder 스무딩 방식으로 ADX, +DI, -DI를 계산한다.
ADX < 20: 평균회귀 레짐, ADX > 25: 추세추종 레짐.
"""

import numpy as np
import pandas as pd


def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """ADX / +DI / -DI 계산.

    1. TR, +DM, -DM 계산
    2. Wilder 스무딩으로 평활화
    3. +DI = 100 × smoothed(+DM) / smoothed(TR)
    4. -DI = 100 × smoothed(-DM) / smoothed(TR)
    5. DX  = 100 × |+DI - -DI| / (+DI + -DI)
    6. ADX = Wilder 스무딩(DX, period)

    워밍업 기간(2 × period 봉) 이전은 NaN을 유지한다.

    Args:
        df: OHLCV DataFrame. 'high', 'low', 'close' 컬럼 필요.
        period: ADX 계산 기간 (기본 14)

    Returns:
        'adx', 'plus_di', 'minus_di' 컬럼이 추가된 DataFrame (복사본).
    """
    df = df.copy()
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = len(df)

    tr_arr = np.full(n, np.nan)
    plus_dm = np.full(n, np.nan)
    minus_dm = np.full(n, np.nan)

    for i in range(1, n):
        if np.isnan(high[i]) or np.isnan(low[i]) or np.isnan(close[i - 1]):
            continue

        # True Range
        tr_arr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

        # Directional Movement
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]

        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

    # Wilder 스무딩 초기값 찾기
    first_valid = next(
        (i for i in range(1, n) if not np.isnan(tr_arr[i])), -1
    )
    if first_valid == -1 or first_valid + period > n:
        df["adx"] = np.nan
        df["plus_di"] = np.nan
        df["minus_di"] = np.nan
        return df

    start = first_valid
    end_init = start + period
    if end_init > n:
        df["adx"] = np.nan
        df["plus_di"] = np.nan
        df["minus_di"] = np.nan
        return df

    # 초기 스무딩 값 (단순 합계)
    smooth_tr = np.full(n, np.nan)
    smooth_plus = np.full(n, np.nan)
    smooth_minus = np.full(n, np.nan)

    init_tr = np.nansum(tr_arr[start:end_init])
    init_pdm = np.nansum(plus_dm[start:end_init])
    init_mdm = np.nansum(minus_dm[start:end_init])

    smooth_tr[end_init - 1] = init_tr
    smooth_plus[end_init - 1] = init_pdm
    smooth_minus[end_init - 1] = init_mdm

    # Wilder 스무딩
    for i in range(end_init, n):
        tr_i = tr_arr[i] if not np.isnan(tr_arr[i]) else 0.0
        pdm_i = plus_dm[i] if not np.isnan(plus_dm[i]) else 0.0
        mdm_i = minus_dm[i] if not np.isnan(minus_dm[i]) else 0.0

        smooth_tr[i] = smooth_tr[i - 1] - smooth_tr[i - 1] / period + tr_i
        smooth_plus[i] = smooth_plus[i - 1] - smooth_plus[i - 1] / period + pdm_i
        smooth_minus[i] = smooth_minus[i - 1] - smooth_minus[i - 1] / period + mdm_i

    # +DI / -DI 계산 (NaN 방어)
    plus_di_arr = np.full(n, np.nan)
    minus_di_arr = np.full(n, np.nan)
    dx_arr = np.full(n, np.nan)

    for i in range(end_init - 1, n):
        str_val = smooth_tr[i]
        if np.isnan(str_val) or str_val == 0:
            continue
        pdi = 100.0 * smooth_plus[i] / str_val
        mdi = 100.0 * smooth_minus[i] / str_val
        plus_di_arr[i] = pdi
        minus_di_arr[i] = mdi

        di_sum = pdi + mdi
        if di_sum != 0:
            dx_arr[i] = 100.0 * abs(pdi - mdi) / di_sum
        else:
            dx_arr[i] = 0.0

    # ADX = Wilder 스무딩(DX, period)
    adx_arr = np.full(n, np.nan)
    dx_start = next(
        (i for i in range(end_init - 1, n) if not np.isnan(dx_arr[i])), -1
    )
    if dx_start == -1 or dx_start + period > n:
        df["adx"] = np.nan
        df["plus_di"] = plus_di_arr
        df["minus_di"] = minus_di_arr
        return df

    adx_end_init = dx_start + period
    if adx_end_init > n:
        df["adx"] = np.nan
        df["plus_di"] = plus_di_arr
        df["minus_di"] = minus_di_arr
        return df

    init_dx = np.nanmean(dx_arr[dx_start:adx_end_init])
    adx_arr[adx_end_init - 1] = init_dx

    for i in range(adx_end_init, n):
        dx_i = dx_arr[i] if not np.isnan(dx_arr[i]) else 0.0
        adx_arr[i] = (adx_arr[i - 1] * (period - 1) + dx_i) / period

    df["adx"] = adx_arr
    df["plus_di"] = plus_di_arr
    df["minus_di"] = minus_di_arr

    return df


__all__ = ["calc_adx"]
