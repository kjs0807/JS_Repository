"""KAMA 적응형 평균회귀/추세추종 전략 모듈.

Efficiency Ratio(ER)로 시장 레짐을 판단해
노이즈 환경(ER < er_mr_threshold)에서는 평균회귀,
추세 환경(ER > er_tf_threshold)에서는 추세추종으로 모드를 전환한다.
"""

import numpy as np
import pandas as pd
from typing import Optional

from strategies.base import BaseStrategy, Signal
from indicators.kama import calc_kama
from indicators.atr import calc_atr


class KAMAMR(BaseStrategy):
    """KAMA 적응형 평균회귀/추세추종 혼합 전략.

    MR 모드 (ER < er_mr_threshold):
        - KAMA ± sigma_mult × σ 이탈 시 평균회귀 진입
        - 스톱: ATR × mr_stop_atr_mult

    TF 모드 (ER > er_tf_threshold):
        - KAMA 상향 기울기 → LONG, 하향 기울기 → SHORT
        - 스톱: ATR × tf_stop_atr_mult

    중간 레짐 (er_mr_threshold ≤ ER ≤ er_tf_threshold):
        - 시그널 생성 안 함 (TRANSITION)

    Attributes:
        name: 전략명
        kama_period: ER 계산 기간
        fast_sc: KAMA 빠른 EMA 기간
        slow_sc: KAMA 느린 EMA 기간
        er_mr_threshold: MR 레짐 ER 상한 (ER < 이 값이면 MR)
        er_tf_threshold: TF 레짐 ER 하한 (ER > 이 값이면 TF)
        sigma_mult: MR 진입 시그마 배수
        atr_period: ATR 계산 기간
        mr_stop_atr_mult: MR 모드 손절 ATR 배수
        tf_stop_atr_mult: TF 모드 손절 ATR 배수
        tf_tp_atr_mult: TF 모드 익절 ATR 배수
    """

    name: str = "KAMAMR"

    def __init__(
        self,
        kama_period: int = 10,
        fast_sc: int = 2,
        slow_sc: int = 30,
        er_mr_threshold: float = 0.3,
        er_tf_threshold: float = 0.6,
        sigma_mult: float = 2.0,
        atr_period: int = 14,
        mr_stop_atr_mult: float = 1.5,
        tf_stop_atr_mult: float = 2.5,
        tf_tp_atr_mult: float = 5.0,
    ) -> None:
        """KAMAMR 초기화.

        Args:
            kama_period: ER 계산 윈도우 기간
            fast_sc: KAMA 빠른 EMA 기간
            slow_sc: KAMA 느린 EMA 기간
            er_mr_threshold: MR 레짐 판단 ER 임계값
            er_tf_threshold: TF 레짐 판단 ER 임계값
            sigma_mult: MR 진입 표준편차 배수
            atr_period: ATR 계산 기간
            mr_stop_atr_mult: MR 모드 손절 ATR 배수
            tf_stop_atr_mult: TF 모드 손절 ATR 배수
            tf_tp_atr_mult: TF 모드 익절 ATR 배수
        """
        self.kama_period = kama_period
        self.fast_sc = fast_sc
        self.slow_sc = slow_sc
        self.er_mr_threshold = er_mr_threshold
        self.er_tf_threshold = er_tf_threshold
        self.sigma_mult = sigma_mult
        self.atr_period = atr_period
        self.mr_stop_atr_mult = mr_stop_atr_mult
        self.tf_stop_atr_mult = tf_stop_atr_mult
        self.tf_tp_atr_mult = tf_tp_atr_mult

    def required_warmup(self) -> int:
        """최소 워밍업 봉 수 반환."""
        return max(self.kama_period + self.slow_sc, self.atr_period) + 20

    def generate_signal(
        self, df: pd.DataFrame, symbol: str
    ) -> Optional[Signal]:
        """ER 기반 레짐 판단 후 MR 또는 TF 시그널 생성.

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
        df = calc_kama(df, period=self.kama_period, fast=self.fast_sc, slow=self.slow_sc)
        df = calc_atr(df, period=self.atr_period)

        cur = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else None

        # NaN 방어
        needed_cols = ["kama", "efficiency_ratio", "atr"]
        for col in needed_cols:
            if col not in df.columns or pd.isna(cur[col]):
                return None

        close = float(cur["close"])
        kama_val = float(cur["kama"])
        er_val = float(cur["efficiency_ratio"])
        atr_val = float(cur["atr"])

        if atr_val <= 0 or np.isnan(atr_val):
            return None
        if np.isnan(er_val):
            return None

        # KAMA 기준 σ 계산 (최근 20봉 close와 KAMA 간 편차)
        kama_series = df["kama"].iloc[-20:]
        close_series = df["close"].iloc[-20:]
        deviation = (close_series - kama_series).dropna()
        sigma = float(deviation.std()) if len(deviation) > 1 else atr_val

        # MR 레짐: ER < er_mr_threshold
        if er_val < self.er_mr_threshold:
            return self._mr_signal(symbol, close, kama_val, sigma, atr_val, er_val)

        # TF 레짐: ER > er_tf_threshold
        if er_val > self.er_tf_threshold:
            if prev is None or pd.isna(prev["kama"]):
                return None
            prev_kama = float(prev["kama"])
            return self._tf_signal(symbol, close, kama_val, prev_kama, atr_val, er_val)

        # TRANSITION 레짐: 시그널 없음
        return None

    def _mr_signal(
        self,
        symbol: str,
        close: float,
        kama_val: float,
        sigma: float,
        atr_val: float,
        er_val: float,
    ) -> Optional[Signal]:
        """MR 모드: KAMA ± sigma_mult × σ 이탈 시 평균회귀 진입.

        Args:
            symbol: 거래 심볼
            close: 현재 종가
            kama_val: 현재 KAMA 값
            sigma: 편차 표준편차
            atr_val: ATR 값
            er_val: Efficiency Ratio

        Returns:
            Signal 또는 None
        """
        if sigma <= 0:
            return None

        upper_band = kama_val + self.sigma_mult * sigma
        lower_band = kama_val - self.sigma_mult * sigma

        if close < lower_band:
            direction = "LONG"
            stop_loss = close - self.mr_stop_atr_mult * atr_val
            take_profit = kama_val  # KAMA 복귀
            strength = min(1.0, (lower_band - close) / (sigma + 1e-9))
            reason = (
                f"KAMA MR LONG: close={close:.4f} < lower={lower_band:.4f} "
                f"ER={er_val:.2f}"
            )
        elif close > upper_band:
            direction = "SHORT"
            stop_loss = close + self.mr_stop_atr_mult * atr_val
            take_profit = kama_val
            strength = min(1.0, (close - upper_band) / (sigma + 1e-9))
            reason = (
                f"KAMA MR SHORT: close={close:.4f} > upper={upper_band:.4f} "
                f"ER={er_val:.2f}"
            )
        else:
            return None

        return Signal(
            symbol=symbol,
            direction=direction,
            strategy_name=self.name,
            strength=max(0.0, min(1.0, strength)),
            entry_price=close,
            stop_loss=stop_loss,
            take_profit=float(take_profit),
            atr=atr_val,
            reason=reason,
        )

    def _tf_signal(
        self,
        symbol: str,
        close: float,
        kama_val: float,
        prev_kama: float,
        atr_val: float,
        er_val: float,
    ) -> Optional[Signal]:
        """TF 모드: KAMA 기울기 방향으로 추세추종 진입.

        Args:
            symbol: 거래 심볼
            close: 현재 종가
            kama_val: 현재 KAMA 값
            prev_kama: 직전 KAMA 값
            atr_val: ATR 값
            er_val: Efficiency Ratio

        Returns:
            Signal 또는 None
        """
        kama_slope = kama_val - prev_kama

        if kama_slope > 0:
            direction = "LONG"
            stop_loss = close - self.tf_stop_atr_mult * atr_val
            take_profit = close + self.tf_tp_atr_mult * atr_val
            strength = min(1.0, er_val)
            reason = (
                f"KAMA TF LONG: KAMA상승={kama_slope:.4f} ER={er_val:.2f}"
            )
        elif kama_slope < 0:
            direction = "SHORT"
            stop_loss = close + self.tf_stop_atr_mult * atr_val
            take_profit = close - self.tf_tp_atr_mult * atr_val
            strength = min(1.0, er_val)
            reason = (
                f"KAMA TF SHORT: KAMA하락={kama_slope:.4f} ER={er_val:.2f}"
            )
        else:
            return None

        return Signal(
            symbol=symbol,
            direction=direction,
            strategy_name=self.name,
            strength=max(0.0, min(1.0, strength)),
            entry_price=close,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=atr_val,
            reason=reason,
        )

    def get_params(self) -> dict:
        """현재 파라미터 반환."""
        return {
            "kama_period": self.kama_period,
            "fast_sc": self.fast_sc,
            "slow_sc": self.slow_sc,
            "er_mr_threshold": self.er_mr_threshold,
            "er_tf_threshold": self.er_tf_threshold,
            "sigma_mult": self.sigma_mult,
            "atr_period": self.atr_period,
            "mr_stop_atr_mult": self.mr_stop_atr_mult,
            "tf_stop_atr_mult": self.tf_stop_atr_mult,
            "tf_tp_atr_mult": self.tf_tp_atr_mult,
        }

    def set_params(self, params: dict) -> None:
        """파라미터 업데이트.

        Args:
            params: 업데이트할 파라미터 딕셔너리
        """
        for key, val in params.items():
            if hasattr(self, key):
                setattr(self, key, val)


__all__ = ["KAMAMR"]
