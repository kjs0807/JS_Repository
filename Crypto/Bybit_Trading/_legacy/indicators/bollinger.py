"""볼린저밴드 (Bollinger Bands) 지표 계산 모듈.

close 기반 Rolling Mean ± N×σ로 상단/중간/하단 밴드를 계산한다.
"""

import numpy as np
import pandas as pd


def calc_bollinger_bands(
    df: pd.DataFrame,
    period: int = 20,
    std: float = 2.0,
) -> pd.DataFrame:
    """볼린저밴드 계산.

    bb_upper = SMA(period) + std × σ
    bb_lower = SMA(period) - std × σ
    bb_width = (bb_upper - bb_lower) / bb_mid
    bb_pctb  = (close - bb_lower) / (bb_upper - bb_lower)

    워밍업 기간(period 봉) 이전은 NaN을 유지한다.

    Args:
        df: OHLCV DataFrame. 최소 'close' 컬럼 필요.
        period: 이동 평균 기간 (기본 20)
        std: 표준편차 배수 (기본 2.0)

    Returns:
        'bb_upper', 'bb_mid', 'bb_lower', 'bb_width', 'bb_pctb' 컬럼이
        추가된 DataFrame (원본 수정 없이 복사본 반환).
    """
    df = df.copy()
    close = df["close"]

    # Rolling SMA 및 표준편차 (min_periods=period로 워밍업 NaN 유지)
    bb_mid = close.rolling(window=period, min_periods=period).mean()
    rolling_std = close.rolling(window=period, min_periods=period).std(ddof=1)

    bb_upper = bb_mid + std * rolling_std
    bb_lower = bb_mid - std * rolling_std

    # 밴드폭: 밴드 폭 / 중심 (NaN 방어)
    band_range = bb_upper - bb_lower
    bb_width = np.where(
        bb_mid.notna() & (bb_mid != 0),
        band_range / bb_mid,
        np.nan,
    )

    # %B: 종가 위치 (0=하단, 1=상단, NaN 방어)
    bb_pctb = np.where(
        band_range.notna() & (band_range != 0),
        (close - bb_lower) / band_range,
        np.nan,
    )

    df["bb_upper"] = bb_upper
    df["bb_mid"] = bb_mid
    df["bb_lower"] = bb_lower
    df["bb_width"] = bb_width
    df["bb_pctb"] = bb_pctb

    return df


__all__ = ["calc_bollinger_bands"]
