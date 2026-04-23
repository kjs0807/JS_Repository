"""indicators/volume.py 단위 테스트."""
import numpy as np
import pandas as pd
from src.core.types import BarSeries
from src.strategies.indicators.volume import (
    vwap, volume_price_divergence, VWAPResult, VolumeDivergenceResult,
)


def _make_series(closes, volumes=None, highs=None, lows=None):
    n = len(closes)
    if volumes is None: volumes = [100.0] * n
    if highs is None: highs = [c + 1 for c in closes]
    if lows is None: lows = [c - 1 for c in closes]
    df = pd.DataFrame({
        "open": closes, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })
    return BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)


class TestVWAP:
    def test_vwap_basic(self):
        closes = [100, 101, 102, 103, 104] * 10
        result = vwap(_make_series(closes), period=20)
        assert isinstance(result, VWAPResult)
        assert len(result.values) == 50

    def test_vwap_warmup_nan(self):
        closes = [100.0] * 20
        result = vwap(_make_series(closes), period=20)
        assert np.isnan(result.values[0])
        assert not np.isnan(result.values[-1])

    def test_vwap_equals_price_when_constant(self):
        closes = [100.0] * 25
        result = vwap(_make_series(closes), period=20)
        valid = result.values[~np.isnan(result.values)]
        np.testing.assert_array_almost_equal(valid, 100.0 * np.ones_like(valid))


class TestVolumeDivergence:
    def test_bearish_divergence(self):
        """가격 신고점 + 거래량 감소."""
        # 19 바 정상, 20번째 바 가격 급등 + 거래량 감소
        closes = [100] * 19 + [120]
        highs = [101] * 19 + [121]
        lows = [99] * 19 + [119]
        volumes = [1000] * 19 + [100]  # 거래량 급감
        result = volume_price_divergence(
            _make_series(closes, volumes, highs, lows), period=19
        )
        assert result.divergence[-1] == -1.0

    def test_no_divergence(self):
        closes = [100] * 25
        result = volume_price_divergence(_make_series(closes), period=20)
        # 가격이 변동 없으니 새 고점/저점 안 찍음
        assert result.divergence[-1] == 0.0
