"""
변동성 분석기
============
일간 수익률 Z-score 계산 + 뉴스 임팩트 스코어링.
|Z|>2 HIGH, |Z|>1 MEDIUM, else LOW.
"""

import logging
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── 기본 임계값 ──
DEFAULT_HIGH_THRESHOLD: float = 2.0
DEFAULT_MEDIUM_THRESHOLD: float = 1.0
DEFAULT_ROLLING_WINDOW: int = 60


class VolatilityAnalyzer:
    """Z-score 기반 변동성 분석기."""

    def __init__(
        self,
        high_threshold: float = DEFAULT_HIGH_THRESHOLD,
        medium_threshold: float = DEFAULT_MEDIUM_THRESHOLD,
        rolling_window: int = DEFAULT_ROLLING_WINDOW,
    ) -> None:
        """임계값과 rolling window 설정.

        Args:
            high_threshold: HIGH 판정 임계값 (기본 2.0).
            medium_threshold: MEDIUM 판정 임계값 (기본 1.0).
            rolling_window: rolling Z-score window (기본 60).
        """
        self.high_threshold = high_threshold
        self.medium_threshold = medium_threshold
        self.rolling_window = rolling_window

    @staticmethod
    def _extract_close_prices(df: pd.DataFrame) -> pd.Series | None:
        """DataFrame에서 close 컬럼을 추출하고 timezone을 제거한 Series를 반환.

        Args:
            df: 가격 DataFrame (close, Close, Adj Close 중 하나 포함).

        Returns:
            timezone-naive close prices Series, 또는 close 컬럼이 없으면 None.
        """
        for col_name in ["close", "Close", "Adj Close"]:
            if col_name in df.columns:
                prices = df[col_name].dropna()
                if prices.index.tz is not None:
                    prices = prices.tz_localize(None)
                return prices
        return None

    def calculate_daily_returns(self, prices: pd.Series) -> pd.Series:
        """일간 수익률(pct_change) 계산.

        Args:
            prices: 종가 Series (index=date).

        Returns:
            일간 수익률 Series (첫 행 NaN 제거).
        """
        if prices is None or prices.empty:
            return pd.Series(dtype=float)
        returns = prices.pct_change().dropna()
        return returns

    def calculate_zscore(
        self, prices: pd.Series, window: int | None = None
    ) -> pd.Series:
        """일간 수익률의 rolling Z-score 계산.

        z = (r - rolling_mean) / rolling_std

        Args:
            prices: 종가 Series (index=date).
            window: rolling window 크기. None이면 self.rolling_window 사용.

        Returns:
            Z-score Series. rolling window 미충족 구간은 NaN.
        """
        if prices is None or prices.empty:
            return pd.Series(dtype=float)

        w = window or self.rolling_window
        returns = self.calculate_daily_returns(prices)

        if returns.empty or len(returns) < 2:
            return pd.Series(dtype=float)

        rolling_mean = returns.rolling(window=w, min_periods=max(w // 2, 2)).mean()
        rolling_std = returns.rolling(window=w, min_periods=max(w // 2, 2)).std()

        # 0으로 나누기 방어
        rolling_std = rolling_std.replace(0, np.nan)

        zscore = (returns - rolling_mean) / rolling_std
        return zscore

    def score_date_impact(
        self,
        target_date: date,
        related_assets: list[str],
        prices_dict: dict[str, pd.DataFrame],
    ) -> dict[str, Any]:
        """특정 날짜의 관련 자산 Z-score로 임팩트 판정.

        알고리즘:
            1. 각 related_asset의 target_date 일간 수익률 Z-score 계산
            2. |Z| 최대값을 대표 Z-score로 선택
            3. |Z| > high → HIGH, |Z| > medium → MEDIUM, else → LOW

        Args:
            target_date: 대상 날짜.
            related_assets: 관련 자산 티커 리스트.
            prices_dict: {ticker: DataFrame(close 포함)}.

        Returns:
            {"importance": "HIGH", "z_score": 2.45,
             "details": {"^TNX": 2.45, "FEDFUNDS": 0.3}}
        """
        details: dict[str, float] = {}
        max_abs_z = 0.0
        max_z = 0.0

        for asset in related_assets:
            df = prices_dict.get(asset)
            if df is None or df.empty:
                continue

            prices = self._extract_close_prices(df)
            if prices is None or prices.empty:
                continue

            zscore_series = self.calculate_zscore(prices)
            if zscore_series.empty:
                continue

            # target_date에 가장 가까운 날짜 찾기 (O(log n))
            td = pd.Timestamp(target_date)
            idx_arr = zscore_series.index
            pos = idx_arr.get_indexer([td], method="nearest")[0]
            closest_idx = None
            if pos >= 0:
                candidate = idx_arr[pos]
                if abs((pd.Timestamp(candidate) - td).days) <= 3:
                    closest_idx = candidate

            if closest_idx is not None:
                z_val = zscore_series.loc[closest_idx]
                if pd.notna(z_val):
                    details[asset] = round(float(z_val), 4)
                    if abs(z_val) > max_abs_z:
                        max_abs_z = abs(z_val)
                        max_z = float(z_val)

        # 임팩트 판정
        if max_abs_z > self.high_threshold:
            importance = "HIGH"
        elif max_abs_z > self.medium_threshold:
            importance = "MEDIUM"
        else:
            importance = "LOW"

        return {
            "importance": importance,
            "z_score": round(max_z, 4),
            "details": details,
        }

    def score_articles(
        self,
        articles: list[dict[str, Any]],
        categories_config: dict[str, dict[str, Any]],
        prices_dict: dict[str, pd.DataFrame],
    ) -> list[dict[str, Any]]:
        """기사 리스트 전체에 대해 임팩트 스코어링.

        Args:
            articles: 기사 dict 리스트.
            categories_config: 카테고리 설정 (related_assets 포함).
            prices_dict: {ticker: DataFrame}.

        Returns:
            importance, z_score, z_score_details 키가 추가된 기사 리스트.
        """
        for article in articles:
            cats = article.get("category", [])
            if isinstance(cats, str):
                cats = [cats]

            # 모든 카테고리의 related_assets 합산
            all_assets: list[str] = []
            for cat_name in cats:
                cat_cfg = categories_config.get(cat_name, {})
                assets = cat_cfg.get("related_assets", [])
                all_assets.extend(assets)

            # 중복 제거
            all_assets = list(set(all_assets))

            if not all_assets:
                article["importance"] = "UNSCORED"
                article["z_score"] = 0.0
                article["z_score_details"] = {}
                continue

            # published 날짜 추출
            pub = article.get("published")
            if pub is None:
                article["importance"] = "UNSCORED"
                article["z_score"] = 0.0
                article["z_score_details"] = {}
                continue

            if hasattr(pub, "date"):
                target_date = pub.date()
            elif isinstance(pub, date):
                target_date = pub
            else:
                article["importance"] = "UNSCORED"
                article["z_score"] = 0.0
                article["z_score_details"] = {}
                continue

            result = self.score_date_impact(target_date, all_assets, prices_dict)
            article["importance"] = result["importance"]
            article["z_score"] = result["z_score"]
            article["z_score_details"] = result["details"]

        return articles

    def get_period_zscore_summary(
        self,
        start_date: date,
        end_date: date,
        prices_dict: dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """기간 내 모든 자산의 일별 Z-score 요약 DataFrame 생성.

        Args:
            start_date: 시작 날짜.
            end_date: 종료 날짜.
            prices_dict: {ticker: DataFrame(close 포함)}.

        Returns:
            DataFrame(index=date, columns=tickers, values=z_scores).
        """
        zscore_data: dict[str, pd.Series] = {}

        for ticker, df in prices_dict.items():
            if df is None or df.empty:
                continue

            prices = self._extract_close_prices(df)
            if prices is None or prices.empty:
                continue

            zscore_series = self.calculate_zscore(prices)
            if not zscore_series.empty:
                zscore_data[ticker] = zscore_series

        if not zscore_data:
            return pd.DataFrame()

        result_df = pd.DataFrame(zscore_data)

        # 기간 필터
        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        result_df = result_df[
            (result_df.index >= start_ts) & (result_df.index <= end_ts)
        ]

        return result_df
