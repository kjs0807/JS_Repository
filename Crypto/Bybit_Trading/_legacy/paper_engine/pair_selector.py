"""동적 페어 선택기 모듈.

DB에 저장된 15m OHLCV 데이터를 읽어 상관계수 + 공적분(Engle-Granger) 검정으로
유효한 페어를 자동 선별한다.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from db.db_manager import DBManager
from config.settings import settings, strategy_params

logger = logging.getLogger(__name__)

# pair_selection.json 저장 경로
_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "pair_selection.json",
)


class PairSelector:
    """동적 페어 선택기.

    13개 코인에서 상관계수 + 공적분(ADF) 검정으로
    유효 페어를 자동 선별한다.

    Attributes:
        db: DBManager 인스턴스
        min_correlation: 최소 수익률 상관계수 (기본 0.5)
        adf_pvalue: Engle-Granger 공적분 검정 유의수준 (기본 0.05)
        max_pairs: 최대 반환 페어 수 (기본 5)
        lookback_bars: 분석에 사용할 최근 봉 수 (기본 5000)
    """

    def __init__(
        self,
        db: DBManager,
        min_correlation: float = 0.5,
        adf_pvalue: Optional[float] = None,
        max_pairs: Optional[int] = None,
        lookback_bars: int = 5000,
    ) -> None:
        """PairSelector 초기화.

        Args:
            db: DBManager 인스턴스
            min_correlation: 최소 수익률 상관계수
            adf_pvalue: 공적분 검정 유의수준 컷오프 (None이면 settings 사용)
            max_pairs: 반환할 최대 페어 수 (None이면 settings 사용)
            lookback_bars: 분석에 사용할 최근 15m 봉 수
        """
        self.db = db
        self.min_correlation = min_correlation
        self.adf_pvalue = adf_pvalue if adf_pvalue is not None else strategy_params.pairs_adf_pvalue
        self.max_pairs = max_pairs if max_pairs is not None else strategy_params.pairs_max_concurrent
        self.lookback_bars = lookback_bars

        # 마지막 선별 결과 캐시
        self._last_result: Optional[dict] = None

    def select_pairs(self) -> List[Tuple[str, str]]:
        """DB에서 15m 데이터를 읽어 유효 페어를 선별한다.

        절차:
        1. settings.symbols의 최근 lookback_bars 봉 로드
        2. 수익률 상관계수 매트릭스 계산
        3. 상관 > min_correlation인 페어 필터링
        4. 각 페어에 Engle-Granger 공적분 검정 (coint)
        5. p-value < adf_pvalue인 페어만 남김
        6. p-value 낮은 순으로 정렬
        7. 상위 max_pairs개 반환

        Returns:
            [(symbol_a, symbol_b), ...] 유효 페어 리스트 (최대 max_pairs개)
        """
        try:
            from config.symbol_manager import get_symbol_manager
            symbols = get_symbol_manager().pairs_universe
        except Exception:
            symbols = settings.symbols
        logger.info("페어 선별 시작: %d개 심볼 분석 (pairs_universe)", len(symbols))

        # 1. 각 심볼의 종가 시계열 로드
        price_series: Dict[str, pd.Series] = {}
        for sym in symbols:
            try:
                df = self.db.get_ohlcv(sym, "15m", limit=self.lookback_bars)
                if df is None or df.empty:
                    logger.warning("데이터 없음: %s", sym)
                    continue
                price_series[sym] = df["close"].dropna()
            except Exception as exc:
                logger.warning("데이터 로드 실패 %s: %s", sym, exc)

        valid_symbols = list(price_series.keys())
        if len(valid_symbols) < 2:
            logger.warning("유효 심볼 %d개 부족 (최소 2개 필요)", len(valid_symbols))
            return []

        # 공통 인덱스로 정렬하여 수익률 계산
        price_df = pd.DataFrame(price_series).dropna(how="all")

        # 최소 100봉 이상 데이터가 있는 심볼만 유지
        price_df = price_df.loc[:, price_df.count() >= 100]
        if price_df.shape[1] < 2:
            logger.warning("충분한 데이터가 있는 심볼이 2개 미만")
            return []

        returns_df = price_df.pct_change().dropna()

        # 2. 수익률 상관계수 매트릭스
        corr_matrix = returns_df.corr()
        syms = corr_matrix.columns.tolist()

        # 3. 상관 > min_correlation인 페어 수집 (중복 제거: i < j)
        candidate_pairs: List[Tuple[str, str, float]] = []
        for i, sym_a in enumerate(syms):
            for j, sym_b in enumerate(syms):
                if j <= i:
                    continue
                corr_val = corr_matrix.at[sym_a, sym_b]
                if pd.isna(corr_val):
                    continue
                if corr_val >= self.min_correlation:
                    candidate_pairs.append((sym_a, sym_b, corr_val))

        logger.info(
            "상관계수 %.2f 초과 페어: %d개 (총 %d 조합)",
            self.min_correlation,
            len(candidate_pairs),
            len(syms) * (len(syms) - 1) // 2,
        )

        # 4 & 5. 공적분 검정
        valid_details: List[dict] = []
        for sym_a, sym_b, corr_val in candidate_pairs:
            pval = self._coint_pvalue(price_df[sym_a], price_df[sym_b])
            if pval < self.adf_pvalue:
                valid_details.append({
                    "pair": f"{sym_a[:3]}-{sym_b[:3]}",
                    "symbol_a": sym_a,
                    "symbol_b": sym_b,
                    "correlation": round(float(corr_val), 4),
                    "coint_pvalue": round(float(pval), 6),
                })
                logger.info(
                    "유효 페어: %s-%s (상관=%.3f, 공적분 p=%.4f)",
                    sym_a, sym_b, corr_val, pval,
                )
            else:
                logger.debug(
                    "페어 제외(공적분 불성립): %s-%s (p=%.4f)", sym_a, sym_b, pval
                )

        # 6. p-value 낮은 순 정렬
        valid_details.sort(key=lambda x: x["coint_pvalue"])

        # 7. 상위 max_pairs개 반환
        selected = valid_details[: self.max_pairs]
        result_pairs = [(d["symbol_a"], d["symbol_b"]) for d in selected]

        logger.info(
            "페어 선별 완료: 검사 %d쌍 -> 유효 %d쌍 -> 선별 %d쌍",
            len(candidate_pairs),
            len(valid_details),
            len(result_pairs),
        )

        # 결과 저장
        self._last_result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pairs": result_pairs,
            "details": selected,
            "symbols_analyzed": len(valid_symbols),
            "pairs_screened": len(candidate_pairs),
            "pairs_valid": len(valid_details),
        }
        self._save_selection_log(self._last_result)

        return result_pairs

    def get_last_selection(self) -> dict:
        """마지막 페어 선별 결과를 반환한다.

        Returns:
            시각, 페어 목록, 검정 결과를 담은 딕셔너리.
            선별 이력이 없으면 빈 딕셔너리.
        """
        if self._last_result is not None:
            return self._last_result
        # 파일에서 복원 시도
        try:
            if os.path.exists(_LOG_PATH):
                with open(_LOG_PATH, "r", encoding="utf-8") as f:
                    self._last_result = json.load(f)
                return self._last_result
        except Exception as exc:
            logger.debug("pair_selection.json 로드 실패: %s", exc)
        return {}

    # -- 내부 헬퍼 ----------------------------------------------------------

    def _coint_pvalue(self, series_a: pd.Series, series_b: pd.Series) -> float:
        """Engle-Granger 공적분 검정 p-value를 반환한다.

        statsmodels.tsa.stattools.coint 사용.
        데이터가 부족하거나 예외 발생 시 1.0을 반환한다 (기각 불가).

        Args:
            series_a: 첫 번째 가격 시계열
            series_b: 두 번째 가격 시계열

        Returns:
            공적분 검정 p-value (0.0 ~ 1.0)
        """
        try:
            from statsmodels.tsa.stattools import coint

            # 공통 인덱스 정렬 후 NaN 제거
            aligned = pd.concat([series_a, series_b], axis=1).dropna()
            if len(aligned) < 50:
                return 1.0

            a_vals = aligned.iloc[:, 0].to_numpy()
            b_vals = aligned.iloc[:, 1].to_numpy()
            _, pvalue, _ = coint(a_vals, b_vals)
            return float(pvalue)

        except ImportError:
            logger.warning("statsmodels 미설치: 공적분 검정 불가 (p=1.0 반환)")
            return 1.0
        except Exception as exc:
            logger.debug("공적분 검정 실패: %s", exc)
            return 1.0

    def _save_selection_log(self, result: dict) -> None:
        """선별 결과를 logs/pair_selection.json에 저장한다.

        Args:
            result: 저장할 선별 결과 딕셔너리
        """
        try:
            os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
            with open(_LOG_PATH, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logger.debug("페어 선별 결과 저장: %s", _LOG_PATH)
        except Exception as exc:
            logger.warning("pair_selection.json 저장 실패: %s", exc)


__all__ = ["PairSelector"]
