"""Keltner Channel 평균회귀 전략 모듈.

가격이 켈트너채널 상/하단을 이탈하면 중심선(EMA) 복귀를 노리는 평균회귀 전략.
ADX < 25 레인지 환경 필터를 적용해 추세 구간에서의 오진입을 방지한다.
"""

import numpy as np
import pandas as pd
from typing import Optional

from strategies.base import BaseStrategy, Signal
from indicators.keltner import calc_keltner_channel
from indicators.atr import calc_atr
from indicators.adx import calc_adx


class KeltnerMR(BaseStrategy):
    """켈트너채널 평균회귀 전략.

    진입: close < kc_lower → LONG (하단 이탈 → 중심선 복귀)
          close > kc_upper → SHORT (상단 이탈 → 중심선 복귀)
    청산: close가 kc_mid(EMA) 복귀 시 (백테스트 엔진에서 처리)
    ADX 필터: ADX < adx_threshold인 경우만 진입 (레인지 환경)
    스톱: ATR × stop_atr_mult (평균회귀 전략)

    Attributes:
        name: 전략명
        kc_period: 켈트너채널 EMA 기간
        kc_mult: 켈트너채널 ATR 배수
        atr_period: ATR 계산 기간
        adx_period: ADX 계산 기간
        adx_threshold: ADX 최대 임계값 (레인지 필터)
        stop_atr_mult: 손절 ATR 배수
    """

    name: str = "KeltnerMR"

    def __init__(
        self,
        kc_period: int = 20,
        kc_mult: float = 1.5,
        atr_period: int = 14,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        stop_atr_mult: float = 1.5,
    ) -> None:
        """KeltnerMR 초기화.

        Args:
            kc_period: 켈트너채널 EMA 기간
            kc_mult: 켈트너채널 ATR 배수
            atr_period: ATR 계산 기간
            adx_period: ADX 계산 기간
            adx_threshold: ADX 레인지 판단 임계값
            stop_atr_mult: 손절 ATR 배수
        """
        self.kc_period = kc_period
        self.kc_mult = kc_mult
        self.atr_period = atr_period
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.stop_atr_mult = stop_atr_mult

    def required_warmup(self) -> int:
        """최소 워밍업 봉 수 반환."""
        return max(self.kc_period, self.atr_period, self.adx_period * 2) + 10

    def generate_signal(
        self, df: pd.DataFrame, symbol: str
    ) -> Optional[Signal]:
        """켈트너채널 이탈 시 평균회귀 시그널 생성.

        Args:
            df: OHLCV + 지표 DataFrame
            symbol: 거래 심볼

        Returns:
            Signal 객체 또는 None
        """
        # 워밍업 구간 체크
        if len(df) < self.required_warmup():
            return None

        # 지표 계산
        df = calc_atr(df, period=self.atr_period)
        df = calc_keltner_channel(df, period=self.kc_period, atr_mult=self.kc_mult, atr_period=self.atr_period)
        df = calc_adx(df, period=self.adx_period)

        cur = df.iloc[-1]

        # NaN 방어
        needed_cols = ["kc_upper", "kc_lower", "kc_mid", "atr", "adx"]
        for col in needed_cols:
            if col not in df.columns or pd.isna(cur[col]):
                return None

        close = float(cur["close"])
        kc_upper = float(cur["kc_upper"])
        kc_lower = float(cur["kc_lower"])
        kc_mid = float(cur["kc_mid"])
        atr_val = float(cur["atr"])
        adx_val = float(cur["adx"])

        if atr_val <= 0 or np.isnan(atr_val):
            return None

        # ADX 레인지 환경 필터: ADX < adx_threshold만 진입
        if adx_val >= self.adx_threshold:
            return None

        # 진입 조건 판단
        if close < kc_lower:
            direction = "LONG"
            stop_loss = close - self.stop_atr_mult * atr_val
            take_profit = kc_mid  # 중심선(EMA) 복귀 목표
            # 채널 하단 침투 깊이로 강도 산출
            channel_width = (kc_upper - kc_lower) / 2 + 1e-9
            strength = min(1.0, (kc_lower - close) / channel_width)
            reason = (
                f"KC하단이탈 LONG: close={close:.4f} < kc_lower={kc_lower:.4f} "
                f"ADX={adx_val:.1f}"
            )
        elif close > kc_upper:
            direction = "SHORT"
            stop_loss = close + self.stop_atr_mult * atr_val
            take_profit = kc_mid
            channel_width = (kc_upper - kc_lower) / 2 + 1e-9
            strength = min(1.0, (close - kc_upper) / channel_width)
            reason = (
                f"KC상단이탈 SHORT: close={close:.4f} > kc_upper={kc_upper:.4f} "
                f"ADX={adx_val:.1f}"
            )
        else:
            return None

        strength = max(0.0, min(1.0, strength))

        return Signal(
            symbol=symbol,
            direction=direction,
            strategy_name=self.name,
            strength=strength,
            entry_price=close,
            stop_loss=stop_loss,
            take_profit=float(take_profit),
            atr=atr_val,
            reason=reason,
        )

    def get_params(self) -> dict:
        """현재 파라미터 반환."""
        return {
            "kc_period": self.kc_period,
            "kc_mult": self.kc_mult,
            "atr_period": self.atr_period,
            "adx_period": self.adx_period,
            "adx_threshold": self.adx_threshold,
            "stop_atr_mult": self.stop_atr_mult,
        }

    def set_params(self, params: dict) -> None:
        """파라미터 업데이트.

        Args:
            params: 업데이트할 파라미터 딕셔너리
        """
        for key, val in params.items():
            if hasattr(self, key):
                setattr(self, key, val)


__all__ = ["KeltnerMR"]
