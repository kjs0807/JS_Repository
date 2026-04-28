"""Pairs Trading (Statistical Arbitrage) 전략 모듈.

OLS 헷지 비율로 스프레드를 계산하고 Z-Score 기반으로 진입/청산한다.
공적분 검정(ADF)으로 유효 페어를 필터링한다.
"""

import logging
import numpy as np
import pandas as pd
from typing import Optional, List, Tuple, Dict

from strategies.base import BaseStrategy, Signal
from indicators.zscore import calc_pair_zscore

logger = logging.getLogger(__name__)

# DEFAULT_PAIRS 제거: config.settings.PAIRS_LIST가 Single Source of Truth


def _adf_pvalue(series: np.ndarray) -> float:
    """ADF 검정 p-value를 반환한다 (간소화 버전).

    statsmodels가 없으면 0.05를 반환하여 항상 통과한다.

    Args:
        series: 시계열 배열

    Returns:
        ADF 검정 p-value
    """
    try:
        from statsmodels.tsa.stattools import adfuller
        clean = series[~np.isnan(series)]
        if len(clean) < 30:
            return 1.0
        result = adfuller(clean, autolag="AIC")
        return float(result[1])
    except ImportError:
        logger.warning("statsmodels 미설치: ADF 검정 생략 (p=0.05 가정)")
        return 0.05
    except Exception as exc:
        logger.warning("ADF 검정 실패: %s", exc)
        return 1.0


class PairsTrading(BaseStrategy):
    """통계적 차익거래 (Pairs Trading) 전략.

    Z-Score > entry_threshold → SHORT spread (숏 A, 롱 B)
    Z-Score < -entry_threshold → LONG spread (롱 A, 숏 B)
    청산: Z-Score가 exit_threshold 이내로 복귀
    스톱: Z-Score가 stop_threshold 초과 (관계 붕괴)

    Attributes:
        name: 전략명
        pairs: 페어 목록 [(symbol_a, symbol_b), ...]
        zscore_window: Z-Score 롤링 윈도우
        entry_threshold: 진입 Z-Score 임계값
        exit_threshold: 청산 Z-Score 임계값
        stop_threshold: 스톱 Z-Score 임계값
        adf_pvalue_threshold: ADF p-value 컷오프
    """

    name: str = "PairsTrading"

    def __init__(
        self,
        pairs: Optional[List[Tuple[str, str]]] = None,
        zscore_window: int = 250,
        entry_threshold: float = 1.75,
        exit_threshold: float = 0.0,
        stop_threshold: float = 4.0,
        adf_pvalue_threshold: float = 0.03,
        stop_pct: float = 0.03,
        tp_pct: float = 0.02,
    ) -> None:
        """PairsTrading 초기화.

        Args:
            pairs: 페어 목록. None이면 빈 리스트.
            zscore_window: 롤링 윈도우 기간
            entry_threshold: 진입 Z-Score 임계값
            exit_threshold: 청산 Z-Score 임계값
            stop_threshold: 스톱 Z-Score 임계값
            adf_pvalue_threshold: ADF 검정 유의수준
            stop_pct: A-leg 스톱 비율 (기본 3%)
            tp_pct: A-leg 익절 비율 (기본 2%)
        """
        self.pairs = pairs if pairs is not None else []
        self.zscore_window = zscore_window
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.stop_threshold = stop_threshold
        self.adf_pvalue_threshold = adf_pvalue_threshold
        self.stop_pct = stop_pct
        self.tp_pct = tp_pct

        # 백테스트 초기에 유효성 검증된 페어 캐시
        self._valid_pairs: Optional[List[Tuple[str, str]]] = None

    def required_warmup(self) -> int:
        """최소 워밍업 봉 수 반환."""
        return self.zscore_window + 10

    def validate_pairs(self, data: Dict[str, pd.DataFrame]) -> List[Tuple[str, str]]:
        """ADF 공적분 검정으로 유효 페어를 필터링한다.

        백테스트 시작 시 한 번 호출해 유효 페어를 캐싱한다.

        Args:
            data: 심볼 → DataFrame 딕셔너리

        Returns:
            유효 페어 목록
        """
        valid = []
        for sym_a, sym_b in self.pairs:
            if sym_a not in data or sym_b not in data:
                logger.info("페어 제외(데이터 없음): %s-%s", sym_a, sym_b)
                continue

            df_a = data[sym_a]
            df_b = data[sym_b]

            try:
                zscore_df = calc_pair_zscore(df_a, df_b, window=self.zscore_window)
            except Exception as exc:
                logger.warning("Z-Score 계산 실패 %s-%s: %s", sym_a, sym_b, exc)
                continue

            spread = zscore_df["spread"].to_numpy()
            p_val = _adf_pvalue(spread)

            if p_val <= self.adf_pvalue_threshold:
                valid.append((sym_a, sym_b))
                logger.info("유효 페어: %s-%s (ADF p=%.4f)", sym_a, sym_b, p_val)
            else:
                logger.info("페어 제외(공적분 불성립): %s-%s (ADF p=%.4f)", sym_a, sym_b, p_val)

        self._valid_pairs = valid
        return valid

    def set_pair_data(
        self,
        pair_data: Dict[str, pd.DataFrame],
    ) -> None:
        """백테스트용 페어 데이터를 주입한다.

        백테스트 엔진은 단일 심볼 인터페이스를 사용하므로,
        페어 파트너 데이터를 미리 주입해두면 generate_signal에서 활용된다.

        Args:
            pair_data: 심볼 → DataFrame 딕셔너리 (예: {"ETHUSDT": df_eth})
        """
        self._injected_pair_data: Dict[str, pd.DataFrame] = pair_data

    def generate_signal(
        self, df: pd.DataFrame, symbol: str
    ) -> Optional[Signal]:
        """단일 심볼 DataFrame으로 시그널을 생성한다.

        페어 파트너 데이터가 주입된 경우(set_pair_data 호출 후) 해당 파트너와
        페어 시그널을 생성한다. 주입된 데이터가 없으면 None을 반환한다.

        백테스트 엔진과의 호환을 위해 구현된 어댑터 메서드다.
        페어 데이터가 없는 경우 generate_signal_pair를 직접 사용하라.

        Args:
            df: symbol의 OHLCV DataFrame
            symbol: 거래 심볼 (페어의 A 심볼)

        Returns:
            Signal 객체 또는 None
        """
        injected: Dict[str, pd.DataFrame] = getattr(self, "_injected_pair_data", {})

        # 이 심볼이 페어의 A 심볼인 페어를 탐색
        for sym_a, sym_b in self.pairs:
            if sym_a != symbol:
                continue
            df_b = injected.get(sym_b)
            if df_b is None:
                continue
            # df_b를 df와 같은 시점까지 슬라이스 (Lookahead bias 방지)
            if not df.empty and not df_b.empty:
                last_time = df.index[-1] if hasattr(df.index, '__len__') else None
                if last_time is not None:
                    try:
                        df_b_slice = df_b[df_b.index <= last_time]
                    except Exception:
                        df_b_slice = df_b
                else:
                    df_b_slice = df_b
            else:
                df_b_slice = df_b

            if df_b_slice.empty:
                continue

            return self.generate_signal_pair(df, df_b_slice, sym_a, sym_b)

        return None

    def generate_signal_pair(
        self,
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        symbol_a: str,
        symbol_b: str,
    ) -> Optional[Signal]:
        """페어 데이터로 Z-Score 기반 시그널을 생성한다.

        Args:
            df_a: 첫 번째 심볼 OHLCV DataFrame
            df_b: 두 번째 심볼 OHLCV DataFrame
            symbol_a: 첫 번째 심볼
            symbol_b: 두 번째 심볼

        Returns:
            Signal 객체 또는 None
        """
        # 워밍업 구간 체크
        if len(df_a) < self.required_warmup() or len(df_b) < self.required_warmup():
            return None

        try:
            zscore_df = calc_pair_zscore(df_a, df_b, window=self.zscore_window)
        except Exception as exc:
            logger.warning("Z-Score 계산 실패: %s", exc)
            return None

        if zscore_df.empty:
            return None

        cur_zscore_row = zscore_df.iloc[-1]
        zscore = cur_zscore_row["zscore"]

        # NaN 방어
        if pd.isna(zscore):
            return None

        zscore = float(zscore)
        close_a = float(df_a.iloc[-1]["close"])

        # ATR 대용: 최근 20봉 종가 표준편차
        recent_close = df_a["close"].iloc[-20:]
        atr_proxy = float(recent_close.std()) if len(recent_close) > 1 else close_a * 0.01

        # 시그널 생성
        if zscore > self.entry_threshold:
            # SHORT spread: A 과고평가 -> A 숏, B 롱
            direction = "SHORT"
            stop_loss = close_a * (1 + self.stop_pct)
            take_profit = close_a * (1 - self.tp_pct)
            strength = min(1.0, (zscore - self.entry_threshold) / (self.stop_threshold - self.entry_threshold))
            reason = f"PairsSHORT {symbol_a}-{symbol_b}: Z={zscore:.2f}>{self.entry_threshold}"
        elif zscore < -self.entry_threshold:
            # LONG spread: A 과저평가 -> A 롱, B 숏
            direction = "LONG"
            stop_loss = close_a * (1 - self.stop_pct)
            take_profit = close_a * (1 + self.tp_pct)
            strength = min(1.0, (-zscore - self.entry_threshold) / (self.stop_threshold - self.entry_threshold))
            reason = f"PairsLONG {symbol_a}-{symbol_b}: Z={zscore:.2f}<-{self.entry_threshold}"
        else:
            return None

        strength = max(0.0, min(1.0, strength))

        return Signal(
            symbol=symbol_a,
            direction=direction,
            strategy_name=self.name,
            strength=strength,
            entry_price=close_a,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=atr_proxy,
            reason=reason,
        )

    def check_cointegration(
        self,
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        window: int = 500,
    ) -> float:
        """롤링 ADF 검정으로 실시간 공적분 p-value를 반환한다.

        Args:
            df_a: 첫 번째 심볼 OHLCV DataFrame
            df_b: 두 번째 심볼 OHLCV DataFrame
            window: 롤링 윈도우 크기

        Returns:
            ADF p-value (낮을수록 공적분 강함). 계산 불가 시 1.0.
        """
        try:
            close_a = df_a["close"].iloc[-window:]
            close_b = df_b["close"].iloc[-window:]
            if len(close_a) < 100 or len(close_b) < 100:
                return 1.0
            cov_mat = np.cov(close_a, close_b, ddof=1)
            spread = close_a.values - close_b.values * (
                cov_mat[0, 1] / cov_mat[1, 1]
            )
            return _adf_pvalue(spread)
        except Exception as exc:
            logger.warning("롤링 공적분 검정 실패: %s", exc)
            return 1.0

    def get_params(self) -> dict:
        """현재 파라미터 반환."""
        return {
            "zscore_window": self.zscore_window,
            "entry_threshold": self.entry_threshold,
            "exit_threshold": self.exit_threshold,
            "stop_threshold": self.stop_threshold,
            "adf_pvalue_threshold": self.adf_pvalue_threshold,
            "stop_pct": self.stop_pct,
            "tp_pct": self.tp_pct,
        }

    def set_params(self, params: dict) -> None:
        """파라미터 업데이트.

        Args:
            params: 업데이트할 파라미터 딕셔너리
        """
        for key, val in params.items():
            if hasattr(self, key):
                setattr(self, key, val)


__all__ = ["PairsTrading"]
