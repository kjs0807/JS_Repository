"""RSI + MACD 복합 시그널 전략 모듈.

RSI 과매수/과매도와 MACD 히스토그램 전환을 동시에 확인하는
모멘텀 반전 전략. ADX 최소 방향성 필터를 적용한다.
"""

import numpy as np
import pandas as pd
from typing import Optional

from strategies.base import BaseStrategy, Signal
from indicators.rsi import calc_rsi
from indicators.macd import calc_macd
from indicators.atr import calc_atr
from indicators.adx import calc_adx


class RSIMACDStrategy(BaseStrategy):
    """RSI + MACD 복합 모멘텀 전략.

    롱 조건:
        1. RSI < rsi_oversold (과매도)
        2. MACD 히스토그램 상승 전환 (음→양 또는 기울기 양전환)
        3. ADX > adx_min_threshold (최소 방향성)

    숏 조건:
        1. RSI > rsi_overbought (과매수)
        2. MACD 히스토그램 하락 전환 (양→음 또는 기울기 음전환)
        3. ADX > adx_min_threshold (최소 방향성)

    스톱: ATR × stop_atr_mult.

    Attributes:
        name: 전략명
        rsi_period: RSI 기간
        rsi_oversold: RSI 과매도 임계값
        rsi_overbought: RSI 과매수 임계값
        macd_fast: MACD 빠른 EMA 기간
        macd_slow: MACD 느린 EMA 기간
        macd_signal: MACD 시그널 EMA 기간
        adx_period: ADX 기간
        adx_min_threshold: ADX 최소 방향성 임계값
        atr_period: ATR 기간
        stop_atr_mult: 손절 ATR 배수
        tp_atr_mult: 익절 ATR 배수
    """

    name: str = "RSIMACDStrategy"

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        adx_period: int = 14,
        adx_min_threshold: float = 20.0,
        atr_period: int = 14,
        stop_atr_mult: float = 2.0,
        tp_atr_mult: float = 4.0,
        exit_mode: str = "fixed",
        tp_pct: float = 0.06,
        sl_pct: float = 0.05,
        leverage: int = 3,
    ) -> None:
        """RSIMACDStrategy 초기화.

        Args:
            rsi_period: RSI 계산 기간
            rsi_oversold: RSI 과매도 임계값
            rsi_overbought: RSI 과매수 임계값
            macd_fast: MACD 빠른 EMA 기간
            macd_slow: MACD 느린 EMA 기간
            macd_signal: MACD 시그널 EMA 기간
            adx_period: ADX 계산 기간
            adx_min_threshold: ADX 최소 방향성 임계값
            atr_period: ATR 계산 기간
            stop_atr_mult: 손절 ATR 배수
            tp_atr_mult: 익절 ATR 배수
            exit_mode: 청산 모드 ("fixed" 또는 "atr")
            tp_pct: 고정 TP 마진 수익률 (exit_mode="fixed" 시 사용)
            sl_pct: 고정 SL 마진 손실률 (exit_mode="fixed" 시 사용)
            leverage: 레버리지 배수 (마진 수익률 → 가격 변동 환산용)
        """
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.adx_period = adx_period
        self.adx_min_threshold = adx_min_threshold
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.exit_mode = exit_mode
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.leverage = leverage

    def required_warmup(self) -> int:
        """최소 워밍업 봉 수 반환."""
        macd_warmup = self.macd_slow + self.macd_signal
        adx_warmup = self.adx_period * 2
        return max(self.rsi_period, macd_warmup, adx_warmup, self.atr_period) + 10

    def generate_signal(
        self, df: pd.DataFrame, symbol: str
    ) -> Optional[Signal]:
        """RSI 과매수/과매도 + MACD 전환 시 시그널 생성.

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
        df = calc_rsi(df, period=self.rsi_period)
        df = calc_macd(df, fast=self.macd_fast, slow=self.macd_slow, signal=self.macd_signal)
        df = calc_atr(df, period=self.atr_period)
        df = calc_adx(df, period=self.adx_period)

        # 최소 2봉 필요 (히스토그램 전환 감지)
        if len(df) < 2:
            return None

        cur = df.iloc[-1]
        prev = df.iloc[-2]

        # NaN 방어
        needed_cols = ["rsi", "histogram", "atr", "adx"]
        for col in needed_cols:
            if col not in df.columns:
                return None
            if pd.isna(cur[col]) or pd.isna(prev[col]):
                return None

        rsi_val = float(cur["rsi"])
        cur_hist = float(cur["histogram"])
        prev_hist = float(prev["histogram"])
        atr_val = float(cur["atr"])
        adx_val = float(cur["adx"])
        close = float(cur["close"])

        if atr_val <= 0 or np.isnan(atr_val):
            return None

        # ADX 최소 방향성 필터
        if adx_val < self.adx_min_threshold:
            return None

        # MACD 히스토그램 상승 전환: 이전 봉 < 0 이거나 기울기 양전환
        hist_turning_up = (prev_hist < 0 and cur_hist > prev_hist) or (
            prev_hist < 0 and cur_hist >= 0
        )
        # MACD 히스토그램 하락 전환
        hist_turning_down = (prev_hist > 0 and cur_hist < prev_hist) or (
            prev_hist > 0 and cur_hist <= 0
        )

        if rsi_val < self.rsi_oversold and hist_turning_up:
            direction = "LONG"
            if self.exit_mode == "fixed":
                price_change_tp = self.tp_pct / self.leverage
                price_change_sl = self.sl_pct / self.leverage
                take_profit = close * (1 + price_change_tp)
                stop_loss = close * (1 - price_change_sl)
            else:
                stop_loss = close - self.stop_atr_mult * atr_val
                take_profit = close + self.tp_atr_mult * atr_val
            # RSI 과매도 깊이 + MACD 기울기로 강도 산출
            rsi_depth = (self.rsi_oversold - rsi_val) / self.rsi_oversold
            hist_momentum = abs(cur_hist - prev_hist) / (abs(prev_hist) + 1e-9)
            strength = min(1.0, (rsi_depth + min(hist_momentum, 1.0)) / 2)
            reason = (
                f"RSI+MACD LONG: RSI={rsi_val:.1f}<{self.rsi_oversold} "
                f"HIST전환{prev_hist:.6f}→{cur_hist:.6f} ADX={adx_val:.1f}"
            )
        elif rsi_val > self.rsi_overbought and hist_turning_down:
            direction = "SHORT"
            if self.exit_mode == "fixed":
                price_change_tp = self.tp_pct / self.leverage
                price_change_sl = self.sl_pct / self.leverage
                take_profit = close * (1 - price_change_tp)
                stop_loss = close * (1 + price_change_sl)
            else:
                stop_loss = close + self.stop_atr_mult * atr_val
                take_profit = close - self.tp_atr_mult * atr_val
            rsi_depth = (rsi_val - self.rsi_overbought) / (100 - self.rsi_overbought)
            hist_momentum = abs(cur_hist - prev_hist) / (abs(prev_hist) + 1e-9)
            strength = min(1.0, (rsi_depth + min(hist_momentum, 1.0)) / 2)
            reason = (
                f"RSI+MACD SHORT: RSI={rsi_val:.1f}>{self.rsi_overbought} "
                f"HIST전환{prev_hist:.6f}→{cur_hist:.6f} ADX={adx_val:.1f}"
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
            "rsi_period": self.rsi_period,
            "rsi_oversold": self.rsi_oversold,
            "rsi_overbought": self.rsi_overbought,
            "macd_fast": self.macd_fast,
            "macd_slow": self.macd_slow,
            "macd_signal": self.macd_signal,
            "adx_period": self.adx_period,
            "adx_min_threshold": self.adx_min_threshold,
            "atr_period": self.atr_period,
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


__all__ = ["RSIMACDStrategy"]
