"""상관관계 분석 — Asset.daily_returns 기반."""

from __future__ import annotations

import numpy as np
import pandas as pd

from optimizer.types import Asset


def build_returns_matrix(assets: list[Asset]) -> pd.DataFrame:
    """Asset 리스트에서 수익률 행렬을 구축한다.

    Args:
        assets: daily_returns가 채워진 Asset 리스트

    Returns:
        columns=symbols, index=dates의 수익률 DataFrame
    """
    data = {}
    for a in assets:
        if a.daily_returns is not None and len(a.daily_returns) > 0:
            data[a.symbol] = a.daily_returns
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data).dropna()


def correlation_matrix(assets: list[Asset]) -> pd.DataFrame:
    """상관관계 행렬을 계산한다."""
    returns = build_returns_matrix(assets)
    if returns.empty:
        return pd.DataFrame()
    return returns.corr()


def max_pairwise_correlation(assets: list[Asset]) -> float:
    """조합 내 최대 쌍별 상관계수를 반환한다.

    1종목이면 0.0을 반환.
    """
    if len(assets) <= 1:
        return 0.0
    corr = correlation_matrix(assets)
    if corr.empty:
        return 0.0
    # 대각선 제외
    n = len(corr)
    max_corr = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            max_corr = max(max_corr, abs(corr.iloc[i, j]))
    return max_corr


def portfolio_diversification_ratio(
    assets: list[Asset],
    weights: dict[str, float] | None = None,
) -> float:
    """포트폴리오 분산비율(DR)을 계산한다.

    DR = (가중 평균 개별 변동성) / (포트폴리오 변동성)
    DR > 1 → 분산 효과 있음.
    """
    returns = build_returns_matrix(assets)
    if returns.empty or len(returns.columns) < 2:
        return 1.0

    symbols = list(returns.columns)
    n = len(symbols)

    if weights is None:
        w = np.array([1.0 / n] * n)
    else:
        w = np.array([weights.get(s, 0.0) for s in symbols])
        total = w.sum()
        if total > 0:
            w = w / total
        else:
            w = np.array([1.0 / n] * n)

    individual_vols = returns.std().values
    weighted_avg_vol = np.dot(w, individual_vols)

    cov_matrix = returns.cov().values
    portfolio_var = np.dot(w, np.dot(cov_matrix, w))
    portfolio_vol = np.sqrt(portfolio_var)

    if portfolio_vol > 0:
        return float(weighted_avg_vol / portfolio_vol)
    return 1.0
