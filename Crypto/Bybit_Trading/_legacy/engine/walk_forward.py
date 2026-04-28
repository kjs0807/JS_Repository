"""Walk-Forward Analysis 모듈.

롤링 윈도우로 IS(In-Sample) 최적화 / OOS(Out-of-Sample) 검증을 반복해
전략의 실전 적용 가능성을 평가한다.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy
from engine.backtest import BacktestEngine, BacktestResult
from engine.annualization import bars_per_year
from config.settings import BacktestConfig

logger = logging.getLogger(__name__)

# Deprecated: 15분봉 기준 하드코딩 상수 (하위 호환용, 직접 사용 금지)
_BARS_PER_DAY = 96           # 24시간 × 4봉/시 (15m 전제 — deprecated)
_BARS_PER_MONTH = _BARS_PER_DAY * 30   # 약 2880봉 (15m 전제 — deprecated)


@dataclass
class WalkForwardWindow:
    """Walk-Forward 단일 윈도우 결과.

    Attributes:
        window_idx: 윈도우 인덱스 (0부터)
        is_start: IS 시작 인덱스
        is_end: IS 종료 인덱스
        oos_start: OOS 시작 인덱스
        oos_end: OOS 종료 인덱스
        is_result: IS 백테스트 결과
        oos_result: OOS 백테스트 결과
        oos_retention: OOS 성과 유지율 (OOS Sharpe / IS Sharpe)
    """

    window_idx: int
    is_start: int
    is_end: int
    oos_start: int
    oos_end: int
    is_result: Optional[BacktestResult] = None
    oos_result: Optional[BacktestResult] = None
    oos_retention: float = 0.0


@dataclass
class WalkForwardResult:
    """Walk-Forward Analysis 전체 결과.

    Attributes:
        strategy_name: 전략명
        symbol: 심볼
        windows: 윈도우별 결과 목록
        avg_oos_retention: 평균 OOS 성과 유지율
        avg_oos_sharpe: 평균 OOS Sharpe Ratio
        avg_is_sharpe: 평균 IS Sharpe Ratio
        oos_trades: OOS 구간 전체 거래 수
    """

    strategy_name: str
    symbol: str
    windows: List[WalkForwardWindow] = field(default_factory=list)
    avg_oos_retention: float = 0.0
    avg_oos_sharpe: float = 0.0
    avg_is_sharpe: float = 0.0
    oos_trades: int = 0


class WalkForwardAnalyzer:
    """롤링 Walk-Forward Analysis 실행기.

    기본 설정: 6개월 IS / 2개월 OOS. timeframe에 따라 봉 수를 동적으로 계산한다.
    각 윈도우에서 IS로 백테스트하고 같은 파라미터로 OOS 검증.
    IS 대비 OOS 성과 유지율을 계산한다.

    Attributes:
        engine: BacktestEngine 인스턴스
        timeframe: 입력 데이터의 봉 주기 ('15m', '30m', '1h', '4h', '1d')
    """

    def __init__(self, timeframe: str = "15m") -> None:
        """WalkForwardAnalyzer 초기화.

        Args:
            timeframe: 입력 데이터의 봉 주기. 기본값 '15m' (하위 호환).
        """
        self.engine = BacktestEngine()
        self.timeframe = timeframe

    def run(
        self,
        strategy: BaseStrategy,
        df: pd.DataFrame,
        is_months: int = 6,
        oos_months: int = 2,
        config: Optional[BacktestConfig] = None,
        symbol: str = "UNKNOWN",
    ) -> WalkForwardResult:
        """Walk-Forward Analysis를 실행한다.

        Args:
            strategy: 평가할 전략 인스턴스
            df: OHLCV DataFrame
            is_months: IS 구간 길이 (개월)
            oos_months: OOS 구간 길이 (개월)
            config: 백테스트 설정
            symbol: 심볼

        Returns:
            WalkForwardResult 객체
        """
        if config is None:
            config = BacktestConfig()

        result = WalkForwardResult(strategy_name=strategy.name, symbol=symbol)
        n = len(df)

        # 봉 수 환산 (timeframe-aware 동적 계산)
        _bars_per_month = bars_per_year(self.timeframe) // 12
        is_bars = is_months * _bars_per_month
        oos_bars = oos_months * _bars_per_month
        window_size = is_bars + oos_bars

        if n < window_size + strategy.required_warmup():
            logger.warning(
                "%s: Walk-Forward 데이터 부족 "
                "(필요 %d봉, 보유 %d봉)",
                strategy.name, window_size + strategy.required_warmup(), n
            )
            return result

        windows: List[WalkForwardWindow] = []
        window_idx = 0

        # 롤링 윈도우 슬라이딩
        start = 0
        while start + window_size <= n:
            is_end = start + is_bars
            oos_end = start + window_size

            # IS 구간 백테스트
            df_is = df.iloc[start:is_end].copy()
            try:
                is_result = self.engine.run(strategy, df_is, config, symbol)
            except Exception as exc:
                logger.warning("IS 백테스트 실패 윈도우%d: %s", window_idx, exc)
                is_result = BacktestResult(strategy_name=strategy.name, symbol=symbol)

            # OOS 구간 백테스트 (IS와 동일 파라미터)
            df_oos = df.iloc[is_end:oos_end].copy()
            try:
                oos_result = self.engine.run(strategy, df_oos, config, symbol)
            except Exception as exc:
                logger.warning("OOS 백테스트 실패 윈도우%d: %s", window_idx, exc)
                oos_result = BacktestResult(strategy_name=strategy.name, symbol=symbol)

            # OOS 성과 유지율 계산
            oos_retention = self._calc_oos_retention(is_result, oos_result)

            wf_window = WalkForwardWindow(
                window_idx=window_idx,
                is_start=start,
                is_end=is_end,
                oos_start=is_end,
                oos_end=oos_end,
                is_result=is_result,
                oos_result=oos_result,
                oos_retention=oos_retention,
            )
            windows.append(wf_window)

            logger.debug(
                "WF 윈도우%d: IS Sharpe=%.2f OOS Sharpe=%.2f 유지율=%.1f%%",
                window_idx,
                is_result.sharpe_ratio,
                oos_result.sharpe_ratio,
                oos_retention * 100,
            )

            # 다음 윈도우: OOS 봉 수만큼 이동 (rolling)
            start += oos_bars
            window_idx += 1

        result.windows = windows

        if windows:
            result.avg_oos_retention = float(
                np.mean([w.oos_retention for w in windows])
            )
            result.avg_oos_sharpe = float(
                np.mean([w.oos_result.sharpe_ratio for w in windows if w.oos_result])
            )
            result.avg_is_sharpe = float(
                np.mean([w.is_result.sharpe_ratio for w in windows if w.is_result])
            )
            result.oos_trades = int(
                sum(w.oos_result.total_trades for w in windows if w.oos_result)
            )

            logger.info(
                "%s Walk-Forward 완료: %d 윈도우, 평균OOS유지율=%.1f%%",
                strategy.name, len(windows), result.avg_oos_retention * 100
            )

        return result

    @staticmethod
    def _calc_oos_retention(
        is_result: BacktestResult, oos_result: BacktestResult
    ) -> float:
        """IS 대비 OOS 성과 유지율을 계산한다.

        Sharpe Ratio 기반. IS Sharpe ≤ 0이면 대비 측정 불가 → 0 반환.
        최솟값 0, 최댓값 1로 클리핑.

        Args:
            is_result: IS 백테스트 결과
            oos_result: OOS 백테스트 결과

        Returns:
            OOS 성과 유지율 (0.0 ~ 1.0)
        """
        is_sharpe = is_result.sharpe_ratio
        oos_sharpe = oos_result.sharpe_ratio

        if is_sharpe <= 0:
            # IS 성과가 0 이하이면 OOS 성과가 양수면 1, 음수면 0
            return 1.0 if oos_sharpe > 0 else 0.0

        retention = oos_sharpe / is_sharpe
        return float(max(0.0, min(1.0, retention)))


__all__ = ["WalkForwardAnalyzer", "WalkForwardResult", "WalkForwardWindow"]
