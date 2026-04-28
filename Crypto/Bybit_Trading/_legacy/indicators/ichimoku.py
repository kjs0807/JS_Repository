"""일목균형표 (Ichimoku Cloud) 지표 계산 모듈.

24/7 암호화폐 시장 최적화 파라미터: tenkan=10, kijun=30, senkou=60.
전통 주식 시장 파라미터는 9, 26, 52.
"""

import numpy as np
import pandas as pd


def calc_ichimoku(
    df: pd.DataFrame,
    tenkan: int = 10,
    kijun: int = 30,
    senkou: int = 60,
) -> pd.DataFrame:
    """일목균형표 계산.

    전환선 (Tenkan-sen):   (최고가[tenkan] + 최저가[tenkan]) / 2
    기준선 (Kijun-sen):    (최고가[kijun] + 최저가[kijun]) / 2
    선행스팬A (Senkou A):  (전환선 + 기준선) / 2, kijun봉 앞으로 시프트
    선행스팬B (Senkou B):  (최고가[senkou] + 최저가[senkou]) / 2, kijun봉 앞으로 시프트
    후행스팬 (Chikou):     현재 종가를 kijun봉 뒤로 시프트

    워밍업 기간(senkou 봉) 이전은 NaN을 유지한다.
    시프트는 DataFrame 인덱스 기반 shift()로 처리한다.

    Args:
        df: OHLCV DataFrame. 'high', 'low', 'close' 컬럼 필요.
        tenkan: 전환선 기간 (기본 10, 암호화폐 최적화)
        kijun: 기준선 기간 (기본 30, 암호화폐 최적화)
        senkou: 선행스팬B 기간 (기본 60, 암호화폐 최적화)

    Returns:
        'tenkan', 'kijun', 'senkou_a', 'senkou_b', 'chikou' 컬럼이
        추가된 DataFrame (복사본).

    Note:
        - senkou_a, senkou_b는 미래 kijun봉 앞쪽에 위치 (shift +kijun)
        - chikou는 kijun봉 과거에 위치 (shift -kijun)
        - 시각화 시 미래 kijun개 봉의 빈 공간이 필요함
    """
    df = df.copy()
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # 전환선: (최고가[tenkan] + 최저가[tenkan]) / 2
    tenkan_max = high.rolling(window=tenkan, min_periods=tenkan).max()
    tenkan_min = low.rolling(window=tenkan, min_periods=tenkan).min()
    tenkan_sen = (tenkan_max + tenkan_min) / 2

    # 기준선: (최고가[kijun] + 최저가[kijun]) / 2
    kijun_max = high.rolling(window=kijun, min_periods=kijun).max()
    kijun_min = low.rolling(window=kijun, min_periods=kijun).min()
    kijun_sen = (kijun_max + kijun_min) / 2

    # 선행스팬A: (전환선 + 기준선) / 2, kijun봉 앞으로 시프트
    senkou_a = ((tenkan_sen + kijun_sen) / 2).shift(kijun)

    # 선행스팬B: (최고가[senkou] + 최저가[senkou]) / 2, kijun봉 앞으로 시프트
    senkou_max = high.rolling(window=senkou, min_periods=senkou).max()
    senkou_min = low.rolling(window=senkou, min_periods=senkou).min()
    senkou_b = ((senkou_max + senkou_min) / 2).shift(kijun)

    # 후행스팬: 현재 종가를 kijun봉 뒤로 시프트 (음수 shift)
    chikou = close.shift(-kijun)

    df["tenkan"] = tenkan_sen
    df["kijun"] = kijun_sen
    df["senkou_a"] = senkou_a
    df["senkou_b"] = senkou_b
    df["chikou"] = chikou

    return df


__all__ = ["calc_ichimoku"]
