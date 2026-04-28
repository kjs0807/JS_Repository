"""켈트너 채널 (Keltner Channel) 지표 계산 모듈.

EMA(period) ± atr_mult × ATR(period)으로 채널을 계산한다.
squeeze_on = BB가 KC 내부에 완전히 포함될 때 True.
"""

import numpy as np
import pandas as pd

from indicators.atr import calc_atr


def calc_keltner_channel(
    df: pd.DataFrame,
    period: int = 20,
    atr_mult: float = 1.5,
    atr_period: int = 14,
) -> pd.DataFrame:
    """켈트너 채널 계산.

    kc_mid   = EMA(close, period)
    kc_upper = kc_mid + atr_mult × ATR(atr_period)
    kc_lower = kc_mid - atr_mult × ATR(atr_period)
    squeeze_on = (bb_upper <= kc_upper) AND (bb_lower >= kc_lower)

    BB 컬럼(bb_upper, bb_lower)이 이미 존재하면 squeeze_on을 계산한다.
    없으면 squeeze_on은 False로 채운다.

    워밍업 기간(max(period, atr_period) 봉) 이전은 NaN을 유지한다.

    Args:
        df: OHLCV DataFrame. 'high', 'low', 'close' 컬럼 필요.
            BB 컬럼(bb_upper, bb_lower)이 있으면 squeeze_on도 계산.
        period: EMA 기간 (기본 20)
        atr_mult: ATR 배수 (기본 1.5)
        atr_period: ATR 계산 기간 (기본 14)

    Returns:
        'kc_upper', 'kc_mid', 'kc_lower', 'squeeze_on' 컬럼이
        추가된 DataFrame (복사본).
    """
    df = df.copy()

    # ATR 계산 (atr 컬럼이 없으면 내부 계산)
    if "atr" not in df.columns:
        df = calc_atr(df, period=atr_period)

    close = df["close"]

    # EMA (중심선)
    kc_mid = close.ewm(span=period, min_periods=period, adjust=False).mean()

    # 채널 상단/하단
    kc_upper = kc_mid + atr_mult * df["atr"]
    kc_lower = kc_mid - atr_mult * df["atr"]

    df["kc_mid"] = kc_mid
    df["kc_upper"] = kc_upper
    df["kc_lower"] = kc_lower

    # squeeze_on: BB가 KC 내부에 완전 포함 여부
    if "bb_upper" in df.columns and "bb_lower" in df.columns:
        squeeze_on = (
            df["bb_upper"].notna()
            & df["bb_lower"].notna()
            & kc_upper.notna()
            & kc_lower.notna()
            & (df["bb_upper"] <= kc_upper)
            & (df["bb_lower"] >= kc_lower)
        )
        df["squeeze_on"] = squeeze_on
    else:
        df["squeeze_on"] = False

    return df


__all__ = ["calc_keltner_channel"]
