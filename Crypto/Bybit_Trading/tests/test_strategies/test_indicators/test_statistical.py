"""indicators/statistical.py 단위 테스트."""
import numpy as np
import pandas as pd
import pytest
from src.core.types import BarSeries
from src.strategies.indicators.statistical import (
    zscore, rolling_correlation, cointegration_test, pca_residuals,
    ZScoreResult, CorrelationResult, CointegrationResult, PCAResult,
)


def _make_series(closes, symbol="BTCUSDT"):
    n = len(closes)
    df = pd.DataFrame({
        "open": closes, "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes], "close": closes,
        "volume": [100.0] * n,
    })
    return BarSeries(symbol=symbol, timeframe="1h", bars=df)


class TestZScore:
    def test_zscore_basic(self):
        np.random.seed(42)
        closes = list(np.cumsum(np.random.randn(100)) + 100)
        result = zscore(_make_series(closes), window=20)
        assert isinstance(result, ZScoreResult)
        assert len(result.values) == 100
        # 앞 19개는 NaN
        assert np.isnan(result.values[0])

    def test_zscore_extreme_values(self):
        closes = [100.0] * 19 + [200.0]  # 마지막이 극단적 이상치
        result = zscore(_make_series(closes), window=20)
        # 마지막 Z는 매우 큼 (std 계산 포함)
        assert not np.isnan(result.values[-1])


class TestRollingCorrelation:
    def test_correlation_perfect_positive(self):
        closes = list(range(100, 150))
        s1 = _make_series(closes, "A")
        s2 = _make_series([c * 2 for c in closes], "B")
        result = rolling_correlation(s1, s2, window=20)
        valid = result.values[~np.isnan(result.values)]
        np.testing.assert_array_almost_equal(valid, np.ones_like(valid), decimal=5)

    def test_correlation_length_mismatch_raises(self):
        s1 = _make_series([1, 2, 3])
        s2 = _make_series([1, 2, 3, 4])
        with pytest.raises(ValueError, match="Series length mismatch"):
            rolling_correlation(s1, s2)


class TestCointegration:
    def test_cointegration_positive(self):
        """공적분 관계 존재 — 두 시계열이 같은 랜덤워크 + 노이즈."""
        np.random.seed(42)
        x = np.cumsum(np.random.randn(200))
        y = 2.0 * x + np.random.randn(200) * 0.5  # 정확히 공적분
        s1 = _make_series(list(y))
        s2 = _make_series(list(x))
        result = cointegration_test(s1, s2)
        assert isinstance(result, CointegrationResult)
        # 공적분이 잘 감지되어야 함
        assert result.p_value < 0.10  # 약한 기준

    def test_cointegration_hedge_ratio(self):
        """hedge ratio 추정 정확도."""
        np.random.seed(123)
        x = np.cumsum(np.random.randn(200))
        y = 2.5 * x + np.random.randn(200) * 0.3
        result = cointegration_test(_make_series(list(y)), _make_series(list(x)))
        assert abs(result.hedge_ratio - 2.5) < 0.3


class TestPCAResiduals:
    def test_pca_basic(self):
        np.random.seed(42)
        # 3개 자산의 수익률, 공통 요인 1개 포함
        common = np.random.randn(100)
        returns = pd.DataFrame({
            "A": common + np.random.randn(100) * 0.1,
            "B": common * 0.8 + np.random.randn(100) * 0.15,
            "C": common * 1.2 + np.random.randn(100) * 0.12,
        })
        result = pca_residuals(returns, n_components=1)
        assert isinstance(result, PCAResult)
        assert result.residuals.shape == (100, 3)
        # 첫 주성분이 분산의 큰 부분 설명
        assert result.explained_variance_ratio[0] > 0.5
