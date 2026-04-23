"""통계 지표 — Z-Score, Correlation, Cointegration, PCA.

통계적 차익거래(StatArb) 전략용.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from src.core.types import BarSeries


@dataclass(frozen=True)
class ZScoreResult:
    """Z-Score 계산 결과."""
    values: np.ndarray
    mean: np.ndarray
    std: np.ndarray


@dataclass(frozen=True)
class CorrelationResult:
    """롤링 상관계수 결과."""
    values: np.ndarray


@dataclass(frozen=True)
class CointegrationResult:
    """공적분 검정 결과 (ADF 기반)."""
    is_cointegrated: bool
    p_value: float
    test_statistic: float
    hedge_ratio: float  # OLS 회귀계수: s1 = alpha + beta * s2
    spread: np.ndarray  # s1 - beta * s2 잔차


@dataclass(frozen=True)
class PCAResult:
    """PCA 잔차 추출 결과."""
    residuals: np.ndarray  # shape (n_samples, n_assets), 각 자산의 잔차
    components: np.ndarray  # shape (n_components, n_assets), 주성분 방향
    explained_variance_ratio: np.ndarray


def zscore(series: BarSeries, window: int = 20) -> ZScoreResult:
    """롤링 Z-Score.

    z[i] = (close[i] - mean[i-window:i]) / std[i-window:i]
    """
    close = series.close
    rolling = close.rolling(window=window, min_periods=window)
    mean = rolling.mean()
    std = rolling.std()
    z = (close - mean) / (std + 1e-10)
    return ZScoreResult(
        values=z.to_numpy(),
        mean=mean.to_numpy(),
        std=std.to_numpy(),
    )


def rolling_correlation(
    series1: BarSeries, series2: BarSeries, window: int = 60
) -> CorrelationResult:
    """두 시계열의 롤링 상관계수.

    두 시리즈는 동일한 길이여야 함.
    """
    if len(series1) != len(series2):
        raise ValueError(f"Series length mismatch: {len(series1)} vs {len(series2)}")
    s1 = series1.close
    s2 = series2.close
    corr = s1.rolling(window=window, min_periods=window).corr(s2)
    return CorrelationResult(values=corr.to_numpy())


def cointegration_test(
    series1: BarSeries, series2: BarSeries, significance: float = 0.05
) -> CointegrationResult:
    """공적분 검정 (ADF 기반).

    1. OLS 회귀로 hedge ratio 추정: s1 = alpha + beta * s2
    2. 잔차(spread)의 ADF 테스트
    3. p-value < significance 이면 공적분 관계 성립

    Args:
        series1: 종속 변수
        series2: 독립 변수
        significance: 유의 수준 (기본 0.05)

    Returns:
        CointegrationResult
    """
    try:
        from statsmodels.tsa.stattools import adfuller
        from statsmodels.regression.linear_model import OLS
        import statsmodels.api as sm
    except ImportError:
        raise ImportError("statsmodels is required for cointegration_test. Install: pip install statsmodels")

    s1 = series1.close.dropna().to_numpy()
    s2 = series2.close.dropna().to_numpy()

    if len(s1) != len(s2):
        n = min(len(s1), len(s2))
        s1 = s1[-n:]
        s2 = s2[-n:]

    if len(s1) < 20:
        return CointegrationResult(
            is_cointegrated=False, p_value=1.0,
            test_statistic=0.0, hedge_ratio=0.0,
            spread=np.array([]),
        )

    # OLS 회귀
    X = sm.add_constant(s2)
    model = OLS(s1, X).fit()
    hedge_ratio = model.params[1]  # beta
    spread = s1 - hedge_ratio * s2

    # ADF 테스트
    adf_result = adfuller(spread, autolag="AIC")
    test_stat = adf_result[0]
    p_value = adf_result[1]
    is_coint = p_value < significance

    return CointegrationResult(
        is_cointegrated=is_coint,
        p_value=float(p_value),
        test_statistic=float(test_stat),
        hedge_ratio=float(hedge_ratio),
        spread=spread,
    )


def pca_residuals(
    returns: pd.DataFrame, n_components: int = 3
) -> PCAResult:
    """PCA로 공통 요인 제거 후 잔차 추출.

    Args:
        returns: 수익률 DataFrame (columns=자산, rows=시간)
        n_components: 제거할 주성분 수

    Returns:
        PCAResult (residuals: (n_samples, n_assets), components, explained_variance_ratio)
    """
    try:
        from sklearn.decomposition import PCA
    except ImportError:
        raise ImportError("scikit-learn required for pca_residuals. Install: pip install scikit-learn")

    # NaN 제거
    clean = returns.dropna()
    if len(clean) < 10 or clean.shape[1] < 2:
        n_samples = len(clean)
        n_assets = clean.shape[1]
        return PCAResult(
            residuals=np.zeros((n_samples, n_assets)),
            components=np.zeros((n_components, n_assets)),
            explained_variance_ratio=np.zeros(n_components),
        )

    X = clean.to_numpy()
    # 표준화 (열별)
    mean = X.mean(axis=0)
    std = X.std(axis=0) + 1e-10
    X_std = (X - mean) / std

    pca = PCA(n_components=min(n_components, X_std.shape[1]))
    factors = pca.fit_transform(X_std)  # (n_samples, n_components)

    # 재구성 → 잔차
    X_reconstructed = pca.inverse_transform(factors)
    residuals = X_std - X_reconstructed

    return PCAResult(
        residuals=residuals,
        components=pca.components_,
        explained_variance_ratio=pca.explained_variance_ratio_,
    )


__all__ = [
    "zscore", "rolling_correlation", "cointegration_test", "pca_residuals",
    "ZScoreResult", "CorrelationResult", "CointegrationResult", "PCAResult",
]
