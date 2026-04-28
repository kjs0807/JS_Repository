"""BB-KC Squeeze 전략 모듈.

볼린저밴드가 켈트너채널 내부에 압축(Squeeze ON)되었다가
해제(Squeeze OFF)될 때 돌파 방향으로 진입하는 추세추종 전략.
암호화폐 변동성 폭발 직전 포착에 적합하다.
"""

import numpy as np
import pandas as pd
from typing import Optional

from strategies.base import BaseStrategy, Signal
from indicators.bollinger import calc_bollinger_bands
from indicators.keltner import calc_keltner_channel
from indicators.atr import calc_atr
from indicators.rsi import calc_rsi


class BBKCSqueeze(BaseStrategy):
    """BB-KC Squeeze 돌파 추세추종 전략.

    Squeeze ON → 변동성 압축 대기.
    Squeeze OFF (해제) → 돌파 방향 진입.

    Attributes:
        name: 전략명
        bb_period: 볼린저밴드 기간
        bb_std: 볼린저밴드 표준편차 배수
        kc_period: 켈트너채널 EMA 기간
        kc_mult: 켈트너채널 ATR 배수
        atr_period: ATR 기간
        rsi_period: RSI 기간
        rsi_filter: RSI 극단 필터 임계값 (과매수/과매도 반대 차단)
        stop_atr_mult: 스톱 ATR 배수
        tp_atr_mult: 익절 ATR 배수
    """

    name: str = "BBKCSqueeze"

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        kc_period: int = 20,
        kc_mult: float = 1.5,
        atr_period: int = 14,
        rsi_period: int = 14,
        rsi_filter: float = 70.0,
        stop_atr_mult: float = 2.5,
        tp_atr_mult: float = 5.0,
        exit_mode: str = "fixed",
        tp_pct: float = 0.06,
        sl_pct: float = 0.07,
        leverage: int = 3,
    ) -> None:
        """BBKCSqueeze 초기화.

        Args:
            bb_period: 볼린저밴드 롤링 기간
            bb_std: 볼린저밴드 표준편차 배수
            kc_period: 켈트너채널 EMA 기간
            kc_mult: 켈트너채널 ATR 배수
            atr_period: ATR 계산 기간
            rsi_period: RSI 계산 기간
            rsi_filter: RSI 극단 임계값 (이 이상/이하면 반대 방향 차단)
            stop_atr_mult: 손절 ATR 배수
            tp_atr_mult: 익절 ATR 배수
            exit_mode: 청산 모드 ("fixed" 또는 "atr")
            tp_pct: 고정 TP 마진 수익률 (exit_mode="fixed" 시 사용)
            sl_pct: 고정 SL 마진 손실률 (exit_mode="fixed" 시 사용)
            leverage: 레버리지 배수 (마진 수익률 → 가격 변동 환산용)
        """
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.kc_period = kc_period
        self.kc_mult = kc_mult
        self.atr_period = atr_period
        self.rsi_period = rsi_period
        self.rsi_filter = rsi_filter
        self.stop_atr_mult = stop_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.exit_mode = exit_mode
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.leverage = leverage

    def required_warmup(self) -> int:
        """최소 워밍업 봉 수 반환."""
        return max(self.bb_period, self.kc_period, self.atr_period, self.rsi_period) + 10

    def generate_signal(
        self, df: pd.DataFrame, symbol: str
    ) -> Optional[Signal]:
        """Squeeze 해제 시점에 돌파 방향으로 시그널 생성.

        Squeeze ON: BB가 KC 내부에 완전 포함 (변동성 압축).
        Squeeze OFF: 직전 봉 Squeeze ON + 현재 봉 Squeeze OFF (해제).
        진입 방향: close > bb_mid → LONG, close < bb_mid → SHORT.
        RSI 필터: LONG인데 RSI > rsi_filter → 차단,
                  SHORT인데 RSI < (100 - rsi_filter) → 차단.

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
        df = calc_bollinger_bands(df, period=self.bb_period, std=self.bb_std)
        df = calc_atr(df, period=self.atr_period)
        df = calc_keltner_channel(df, period=self.kc_period, atr_mult=self.kc_mult, atr_period=self.atr_period)
        df = calc_rsi(df, period=self.rsi_period)

        # 마지막 2개 봉 참조
        if len(df) < 2:
            return None

        cur = df.iloc[-1]
        prev = df.iloc[-2]

        # NaN 방어
        needed_cols = ["squeeze_on", "bb_mid", "atr", "rsi"]
        for col in needed_cols:
            if col not in df.columns:
                return None
            if pd.isna(cur[col]) or pd.isna(prev[col]):
                return None

        # Squeeze 해제 감지: 직전 봉 ON, 현재 봉 OFF
        prev_squeeze_on = bool(prev["squeeze_on"])
        cur_squeeze_on = bool(cur["squeeze_on"])

        if not (prev_squeeze_on and not cur_squeeze_on):
            return None

        close = float(cur["close"])
        bb_mid = float(cur["bb_mid"])
        atr_val = float(cur["atr"])
        rsi_val = float(cur["rsi"])

        if atr_val <= 0 or np.isnan(atr_val):
            return None

        # 진입 방향 결정
        if close > bb_mid:
            direction = "LONG"
            # RSI 극단 과매수 → LONG 차단
            if rsi_val > self.rsi_filter:
                return None
            if self.exit_mode == "fixed":
                price_change_tp = self.tp_pct / self.leverage
                price_change_sl = self.sl_pct / self.leverage
                take_profit = close * (1 + price_change_tp)
                stop_loss = close * (1 - price_change_sl)
            else:
                stop_loss = close - self.stop_atr_mult * atr_val
                take_profit = close + self.tp_atr_mult * atr_val
            strength = min(1.0, (close - bb_mid) / (atr_val + 1e-9))
        elif close < bb_mid:
            direction = "SHORT"
            # RSI 극단 과매도 → SHORT 차단
            if rsi_val < (100.0 - self.rsi_filter):
                return None
            if self.exit_mode == "fixed":
                price_change_tp = self.tp_pct / self.leverage
                price_change_sl = self.sl_pct / self.leverage
                take_profit = close * (1 - price_change_tp)
                stop_loss = close * (1 + price_change_sl)
            else:
                stop_loss = close + self.stop_atr_mult * atr_val
                take_profit = close - self.tp_atr_mult * atr_val
            strength = min(1.0, (bb_mid - close) / (atr_val + 1e-9))
        else:
            return None

        strength = max(0.0, min(1.0, strength))

        return Signal(
            symbol=symbol,
            direction=direction,
            strategy_name=self.name,
            strength=strength,
            entry_price=close,         # 다음 봉 open에서 체결
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=atr_val,
            reason=f"Squeeze해제 {direction}: close={close:.4f} bb_mid={bb_mid:.4f} RSI={rsi_val:.1f}",
        )

    def get_params(self) -> dict:
        """현재 파라미터 반환."""
        return {
            "bb_period": self.bb_period,
            "bb_std": self.bb_std,
            "kc_period": self.kc_period,
            "kc_mult": self.kc_mult,
            "atr_period": self.atr_period,
            "rsi_period": self.rsi_period,
            "rsi_filter": self.rsi_filter,
            "stop_atr_mult": self.stop_atr_mult,
            "tp_atr_mult": self.tp_atr_mult,
            "exit_mode": self.exit_mode,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "leverage": self.leverage,
        }

    def set_params(self, params: dict) -> None:
        """파라미터 업데이트.

        Args:
            params: 업데이트할 파라미터 딕셔너리
        """
        for key, val in params.items():
            if hasattr(self, key):
                setattr(self, key, val)


__all__ = ["BBKCSqueeze"]
