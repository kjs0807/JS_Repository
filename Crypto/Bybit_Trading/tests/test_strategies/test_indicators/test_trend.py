"""indicators/trend.py 단위 테스트."""
import numpy as np
import pandas as pd
from src.core.types import BarSeries
from src.strategies.indicators.trend import ema, sma, EMAResult, SMAResult

def _make_series(closes):
    n = len(closes)
    df = pd.DataFrame({"open": closes, "high": [c+10 for c in closes],
        "low": [c-10 for c in closes], "close": closes, "volume": [100.0]*n})
    return BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)

class TestSMA:
    def test_sma_basic(self):
        result = sma(_make_series([10,20,30,40,50]), period=3)
        assert isinstance(result, SMAResult)
        assert np.isnan(result.values[0])
        assert np.isnan(result.values[1])
        assert abs(result.values[2] - 20.0) < 0.01
        assert abs(result.values[3] - 30.0) < 0.01
        assert abs(result.values[4] - 40.0) < 0.01

    def test_sma_period_1(self):
        result = sma(_make_series([100,200,300]), period=1)
        np.testing.assert_array_almost_equal(result.values, [100,200,300])

    def test_sma_length(self):
        assert len(sma(_make_series([1.0]*20), period=5).values) == 20

class TestEMA:
    def test_ema_basic(self):
        result = ema(_make_series([10,20,30,40,50,60]), period=3)
        assert isinstance(result, EMAResult)
        assert np.isnan(result.values[0])
        assert np.isnan(result.values[1])
        assert not np.isnan(result.values[2])

    def test_ema_converges_on_constant(self):
        result = ema(_make_series([100.0]*20), period=5)
        valid = result.values[~np.isnan(result.values)]
        np.testing.assert_array_almost_equal(valid, [100.0]*len(valid))

    def test_ema_length(self):
        assert len(ema(_make_series([1.0]*20), period=10).values) == 20

    def test_ema_reacts_faster_than_sma(self):
        closes = [100.0]*10 + [200.0]
        assert ema(_make_series(closes), period=5).values[-1] > sma(_make_series(closes), period=5).values[-1]
