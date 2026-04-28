"""일목균형표 (Ichimoku Cloud) 전략 모듈.

24/7 암호화폐 시장 최적화 파라미터 (10, 30, 60)를 사용하는 추세추종 전략.
구름, 전환선/기준선 크로스, 후행스팬 3중 확인으로 진입한다.
"""

import numpy as np
import pandas as pd
from typing import Optional

from strategies.base import BaseStrategy, Signal
from indicators.ichimoku import calc_ichimoku
from indicators.atr import calc_atr


class IchimokuCloud(BaseStrategy):
    """일목균형표 추세추종 전략.

    롱 조건:
        1. close > max(senkou_a, senkou_b)  (구름 위)
        2. tenkan > kijun                    (전환선 > 기준선)
        3. chikou > 26봉전 가격              (후행스팬 확인)

    숏 조건:
        1. close < min(senkou_a, senkou_b)  (구름 아래)
        2. tenkan < kijun                    (전환선 < 기준선)
        3. chikou < 26봉전 가격              (후행스팬 확인)

    구름 두께로 시그널 강도 산출.
    스톱: ATR × stop_atr_mult (추세추종 전략).

    Attributes:
        name: 전략명
        tenkan: 전환선 기간
        kijun: 기준선 기간
        senkou: 선행스팬B 기간
        atr_period: ATR 기간
        stop_atr_mult: 손절 ATR 배수
        tp_atr_mult: 익절 ATR 배수
    """

    name: str = "IchimokuCloud"

    def __init__(
        self,
        tenkan: int = 10,
        kijun: int = 30,
        senkou: int = 60,
        atr_period: int = 14,
        stop_atr_mult: float = 2.5,
        tp_atr_mult: float = 5.0,
    ) -> None:
        """IchimokuCloud 초기화.

        Args:
            tenkan: 전환선 기간 (암호화폐 최적화: 10)
            kijun: 기준선 기간 (암호화폐 최적화: 30)
            senkou: 선행스팬B 기간 (암호화폐 최적화: 60)
            atr_period: ATR 계산 기간
            stop_atr_mult: 손절 ATR 배수
            tp_atr_mult: 익절 ATR 배수
        """
        self.tenkan = tenkan
        self.kijun = kijun
        self.senkou = senkou
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.tp_atr_mult = tp_atr_mult

    def required_warmup(self) -> int:
        """최소 워밍업 봉 수 반환.

        선행스팬이 kijun봉 앞으로 시프트되므로 senkou + kijun + 여유분 필요.
        """
        return self.senkou + self.kijun + 10

    def generate_signal(
        self, df: pd.DataFrame, symbol: str
    ) -> Optional[Signal]:
        """일목균형표 3중 조건 충족 시 시그널 생성.

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
        df = calc_ichimoku(df, tenkan=self.tenkan, kijun=self.kijun, senkou=self.senkou)
        df = calc_atr(df, period=self.atr_period)

        cur = df.iloc[-1]

        # NaN 방어
        needed_cols = ["tenkan", "kijun", "senkou_a", "senkou_b", "atr"]
        for col in needed_cols:
            if col not in df.columns or pd.isna(cur[col]):
                return None

        close = float(cur["close"])
        tenkan_val = float(cur["tenkan"])
        kijun_val = float(cur["kijun"])
        senkou_a = float(cur["senkou_a"])
        senkou_b = float(cur["senkou_b"])
        atr_val = float(cur["atr"])

        if atr_val <= 0 or np.isnan(atr_val):
            return None

        # 후행스팬 확인: kijun봉 이전 가격과 비교
        # chikou는 현재 close를 -kijun 시프트한 값이므로
        # 현재 close를 kijun봉 전 인덱스의 close와 비교
        if len(df) <= self.kijun:
            return None
        past_close = float(df.iloc[-self.kijun - 1]["close"])

        # 구름 상단/하단
        cloud_top = max(senkou_a, senkou_b)
        cloud_bottom = min(senkou_a, senkou_b)
        cloud_thickness = abs(senkou_a - senkou_b)

        # 롱 조건: 구름 위 + 전환선 > 기준선 + 후행스팬 확인
        long_condition = (
            close > cloud_top
            and tenkan_val > kijun_val
            and close > past_close  # 후행스팬 확인 (현재 close > kijun봉 전 가격)
        )

        # 숏 조건: 구름 아래 + 전환선 < 기준선 + 후행스팬 확인
        short_condition = (
            close < cloud_bottom
            and tenkan_val < kijun_val
            and close < past_close
        )

        if long_condition:
            direction = "LONG"
            stop_loss = close - self.stop_atr_mult * atr_val
            take_profit = close + self.tp_atr_mult * atr_val
            # 구름 두께 / ATR로 강도 산출 (두꺼울수록 강한 추세)
            strength = min(1.0, cloud_thickness / (atr_val * 2 + 1e-9))
            reason = (
                f"Ichimoku LONG: close={close:.4f} > cloud_top={cloud_top:.4f} "
                f"전환={tenkan_val:.4f}>기준={kijun_val:.4f}"
            )
        elif short_condition:
            direction = "SHORT"
            stop_loss = close + self.stop_atr_mult * atr_val
            take_profit = close - self.tp_atr_mult * atr_val
            strength = min(1.0, cloud_thickness / (atr_val * 2 + 1e-9))
            reason = (
                f"Ichimoku SHORT: close={close:.4f} < cloud_bottom={cloud_bottom:.4f} "
                f"전환={tenkan_val:.4f}<기준={kijun_val:.4f}"
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
            take_profit=take_profit,
            atr=atr_val,
            reason=reason,
        )

    def get_params(self) -> dict:
        """현재 파라미터 반환."""
        return {
            "tenkan": self.tenkan,
            "kijun": self.kijun,
            "senkou": self.senkou,
            "atr_period": self.atr_period,
            "stop_atr_mult": self.stop_atr_mult,
            "tp_atr_mult": self.tp_atr_mult,
        }

    def set_params(self, params: dict) -> None:
        """파라미터 업데이트.

        Args:
            params: 업데이트할 파라미터 딕셔너리
        """
        for key, val in params.items():
            if hasattr(self, key):
                setattr(self, key, val)


__all__ = ["IchimokuCloud"]
