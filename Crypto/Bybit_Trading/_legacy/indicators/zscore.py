"""페어 스프레드 Z-Score 지표 계산 모듈.

OLS 회귀로 헷지 비율을 추정하고 스프레드 Z-Score를 계산한다.
Pairs Trading (Statistical Arbitrage) 전략에 사용된다.
"""

import numpy as np
import pandas as pd


def calc_pair_zscore(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    window: int = 250,
) -> pd.DataFrame:
    """페어 스프레드 Z-Score 계산.

    log(price_A) - hedge_ratio × log(price_B) 스프레드의 Z-Score.
    hedge_ratio는 rolling OLS(window 봉)로 추정한다.

    spread      = log_a - hedge_ratio × log_b
    zscore      = (spread - rolling_mean(spread)) / rolling_std(spread)
    hedge_ratio = cov(log_a, log_b) / var(log_b)  [rolling OLS]

    워밍업 기간(window 봉) 이전은 NaN을 유지한다.

    Args:
        df_a: 첫 번째 자산 DataFrame. 'close' 컬럼 필요.
        df_b: 두 번째 자산 DataFrame. 'close' 컬럼 필요.
            df_a와 인덱스가 동일해야 한다.
        window: rolling OLS 및 Z-Score 계산 윈도우 (기본 250)

    Returns:
        'spread', 'zscore', 'hedge_ratio' 컬럼을 가진 새 DataFrame.
        인덱스는 df_a의 인덱스를 따른다.

    Note:
        - 두 DataFrame의 인덱스가 다르면 내부에서 align() 처리한다.
        - 가격이 0 이하인 봉은 NaN으로 처리한다.
    """
    # 인덱스 정렬
    close_a, close_b = df_a["close"].align(df_b["close"], join="inner")

    # NaN 방어: 0 이하 가격 → NaN
    close_a = close_a.where(close_a > 0, other=np.nan)
    close_b = close_b.where(close_b > 0, other=np.nan)

    log_a = np.log(close_a)
    log_b = np.log(close_b)

    n = len(log_a)

    hedge_ratio_arr = np.full(n, np.nan)
    spread_arr = np.full(n, np.nan)

    # Rolling OLS: hedge_ratio = Cov(log_a, log_b) / Var(log_b)
    log_a_arr = log_a.to_numpy(dtype=float)
    log_b_arr = log_b.to_numpy(dtype=float)

    for i in range(window - 1, n):
        a_window = log_a_arr[i - window + 1: i + 1]
        b_window = log_b_arr[i - window + 1: i + 1]

        # NaN이 너무 많으면 스킵
        valid_mask = ~(np.isnan(a_window) | np.isnan(b_window))
        if valid_mask.sum() < window // 2:
            continue

        a_w = a_window[valid_mask]
        b_w = b_window[valid_mask]

        var_b = np.var(b_w, ddof=1)
        if var_b == 0 or np.isnan(var_b):
            continue

        cov_ab = np.cov(a_w, b_w, ddof=1)[0, 1]
        beta = cov_ab / var_b
        hedge_ratio_arr[i] = beta

        # 스프레드: log_a - beta × log_b
        if not np.isnan(log_a_arr[i]) and not np.isnan(log_b_arr[i]):
            spread_arr[i] = log_a_arr[i] - beta * log_b_arr[i]

    # Z-Score: rolling mean/std of spread
    # min_periods=30으로 부분 윈도우도 계산 (OLS 추정에는 window 전체 사용)
    zscore_min_periods = max(30, window // 5)
    spread_series = pd.Series(spread_arr, index=close_a.index)
    rolling_mean = spread_series.rolling(window=window, min_periods=zscore_min_periods).mean()
    rolling_std = spread_series.rolling(window=window, min_periods=zscore_min_periods).std(ddof=1)

    zscore_arr = np.where(
        rolling_std.notna() & (rolling_std != 0),
        (spread_series - rolling_mean) / rolling_std,
        np.nan,
    )

    result = pd.DataFrame(
        {
            "spread": spread_arr,
            "zscore": zscore_arr,
            "hedge_ratio": hedge_ratio_arr,
        },
        index=close_a.index,
    )

    return result


__all__ = ["calc_pair_zscore"]
