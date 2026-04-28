"""레짐 감지기 모듈.

ADX와 BB-KC Squeeze를 조합해 시장 레짐을 MR/TF/TRANSITION으로 분류한다.
백테스트 엔진과 포워드 테스트에서 전략 선택에 활용된다.
"""

import pandas as pd
from typing import Literal

from indicators.bollinger import calc_bollinger_bands
from indicators.keltner import calc_keltner_channel
from indicators.atr import calc_atr
from indicators.adx import calc_adx


RegimeType = Literal["MR", "TF", "TRANSITION"]


class RegimeDetector:
    """ADX + BB-KC Squeeze 기반 시장 레짐 감지기.

    레짐 판단 규칙:
        - Squeeze ON + ADX < adx_mr_threshold → "MR" (평균회귀)
        - Squeeze OFF + ADX > adx_tf_threshold → "TF" (추세추종)
        - 기타 → "TRANSITION" (중간 레짐)

    Attributes:
        bb_period: 볼린저밴드 기간
        bb_std: 볼린저밴드 표준편차 배수
        kc_period: 켈트너채널 EMA 기간
        kc_mult: 켈트너채널 ATR 배수
        atr_period: ATR 기간
        adx_period: ADX 기간
        adx_mr_threshold: MR 레짐 ADX 상한
        adx_tf_threshold: TF 레짐 ADX 하한
    """

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        kc_period: int = 20,
        kc_mult: float = 1.5,
        atr_period: int = 14,
        adx_period: int = 14,
        adx_mr_threshold: float = 20.0,
        adx_tf_threshold: float = 25.0,
    ) -> None:
        """RegimeDetector 초기화.

        Args:
            bb_period: 볼린저밴드 기간
            bb_std: 볼린저밴드 표준편차 배수
            kc_period: 켈트너채널 EMA 기간
            kc_mult: 켈트너채널 ATR 배수
            atr_period: ATR 기간
            adx_period: ADX 기간
            adx_mr_threshold: MR 레짐 ADX 상한 (ADX < 이 값이면 MR 후보)
            adx_tf_threshold: TF 레짐 ADX 하한 (ADX > 이 값이면 TF 후보)
        """
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.kc_period = kc_period
        self.kc_mult = kc_mult
        self.atr_period = atr_period
        self.adx_period = adx_period
        self.adx_mr_threshold = adx_mr_threshold
        self.adx_tf_threshold = adx_tf_threshold

    def required_warmup(self) -> int:
        """최소 워밍업 봉 수 반환."""
        return max(self.bb_period, self.kc_period, self.adx_period * 2, self.atr_period) + 10

    def detect(self, df: pd.DataFrame) -> RegimeType:
        """현재 봉 기준으로 시장 레짐을 감지한다.

        ADX + BB-KC Squeeze를 계산하고 레짐을 분류한다.
        워밍업 미달 또는 NaN 발생 시 "TRANSITION"을 반환한다.

        Args:
            df: OHLCV DataFrame (최소 required_warmup 봉 이상)

        Returns:
            "MR" | "TF" | "TRANSITION"
        """
        # 워밍업 구간 체크
        if len(df) < self.required_warmup():
            return "TRANSITION"

        try:
            df = calc_bollinger_bands(df, period=self.bb_period, std=self.bb_std)
            df = calc_atr(df, period=self.atr_period)
            df = calc_keltner_channel(df, period=self.kc_period, atr_mult=self.kc_mult, atr_period=self.atr_period)
            df = calc_adx(df, period=self.adx_period)
        except Exception:
            return "TRANSITION"

        cur = df.iloc[-1]

        # NaN 방어
        for col in ["squeeze_on", "adx"]:
            if col not in df.columns or cur[col] is None:
                return "TRANSITION"
            try:
                import math
                if math.isnan(float(cur[col])):
                    return "TRANSITION"
            except (TypeError, ValueError):
                return "TRANSITION"

        squeeze_on = bool(cur["squeeze_on"])
        adx_val = float(cur["adx"])

        # 레짐 분류
        if squeeze_on and adx_val < self.adx_mr_threshold:
            return "MR"
        elif not squeeze_on and adx_val > self.adx_tf_threshold:
            return "TF"
        else:
            return "TRANSITION"

    def detect_series(self, df: pd.DataFrame) -> pd.Series:
        """전체 DataFrame에 대해 봉별 레짐을 계산한다.

        백테스트 전처리용 벡터화 계산.

        Args:
            df: OHLCV DataFrame

        Returns:
            레짐 문자열 Series ("MR" | "TF" | "TRANSITION")
        """
        try:
            df = calc_bollinger_bands(df, period=self.bb_period, std=self.bb_std)
            df = calc_atr(df, period=self.atr_period)
            df = calc_keltner_channel(df, period=self.kc_period, atr_mult=self.kc_mult, atr_period=self.atr_period)
            df = calc_adx(df, period=self.adx_period)
        except Exception:
            return pd.Series(["TRANSITION"] * len(df), index=df.index)

        warmup = self.required_warmup()

        def _classify_row(row: pd.Series) -> str:
            """단일 행에 대한 레짐 분류."""
            try:
                squeeze = bool(row.get("squeeze_on", False))
                adx = float(row.get("adx", float("nan")))
                import math
                if math.isnan(adx):
                    return "TRANSITION"
            except (TypeError, ValueError):
                return "TRANSITION"

            if squeeze and adx < self.adx_mr_threshold:
                return "MR"
            elif not squeeze and adx > self.adx_tf_threshold:
                return "TF"
            return "TRANSITION"

        regimes = []
        for i in range(len(df)):
            if i < warmup:
                regimes.append("TRANSITION")
            else:
                regimes.append(_classify_row(df.iloc[i]))

        return pd.Series(regimes, index=df.index, dtype=str)


__all__ = ["RegimeDetector", "RegimeType"]
