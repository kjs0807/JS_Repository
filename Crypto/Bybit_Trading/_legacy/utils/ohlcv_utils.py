"""OHLCV 데이터 유틸리티 - 리샘플링, 변환 등 공통 함수."""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd


def resample_ohlcv(df: pd.DataFrame, factor: int) -> pd.DataFrame:
    """15분봉을 N배로 리샘플링한다.

    Args:
        df: OHLCV DataFrame (open_time, open, high, low, close, volume 컬럼)
        factor: 리샘플링 배수 (2=30분, 4=1시간, 16=4시간)

    Returns:
        리샘플링된 DataFrame
    """
    n = len(df)
    trim = n - (n % factor)
    if trim == 0:
        return pd.DataFrame(columns=df.columns)
    dfc = df.iloc[:trim].copy()
    groups = np.arange(trim) // factor
    return pd.DataFrame({
        "open_time": dfc.groupby(groups)["open_time"].first().values,
        "open": dfc.groupby(groups)["open"].first().values,
        "high": dfc.groupby(groups)["high"].max().values,
        "low": dfc.groupby(groups)["low"].min().values,
        "close": dfc.groupby(groups)["close"].last().values,
        "volume": dfc.groupby(groups)["volume"].sum().values,
    }).reset_index(drop=True)


def merge_bars(bars: List[dict]) -> dict:
    """여러 봉을 하나로 합친다.

    Args:
        bars: 봉 딕셔너리 리스트 (open_time, open, high, low, close, volume)

    Returns:
        합쳐진 단일 봉 딕셔너리
    """
    if not bars:
        return {}
    return {
        "open_time": bars[0]["open_time"],
        "open": bars[0]["open"],
        "high": max(b["high"] for b in bars),
        "low": min(b["low"] for b in bars),
        "close": bars[-1]["close"],
        "volume": sum(b["volume"] for b in bars),
    }


def bars_to_df(bars: List[dict]) -> pd.DataFrame:
    """봉 리스트를 DataFrame으로 변환한다.

    Args:
        bars: 봉 딕셔너리 리스트

    Returns:
        OHLCV DataFrame
    """
    if not bars:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])
    return pd.DataFrame(bars)


def df_to_bars(df: pd.DataFrame) -> List[dict]:
    """DataFrame을 봉 리스트로 변환한다 (iterrows 대신 to_dict 사용).

    Args:
        df: OHLCV DataFrame

    Returns:
        봉 딕셔너리 리스트
    """
    if df is None or df.empty:
        return []
    cols = ["open_time", "open", "high", "low", "close", "volume"]
    available_cols = [c for c in cols if c in df.columns]
    records = df[available_cols].to_dict("records")
    # open_time을 int로 보정
    for r in records:
        if "open_time" in r:
            r["open_time"] = int(r["open_time"])
        for key in ("open", "high", "low", "close", "volume"):
            if key in r:
                r[key] = float(r[key])
    return records


def is_boundary(open_time_ms: int, interval_ms: int) -> bool:
    """주어진 타임스탬프가 interval 경계인지 확인한다.

    Args:
        open_time_ms: 봉 시작 시각 (Unix 밀리초)
        interval_ms: 간격 밀리초 (1800000=30m, 14400000=4H)

    Returns:
        경계이면 True
    """
    return open_time_ms % interval_ms == 0
