"""indicators/oscillator.py 단위 테스트."""
import numpy as np
import pandas as pd
from src.core.types import BarSeries
from src.strategies.indicators.oscillator import rsi, macd, RSIResult, MACDResult

def _make_series(closes):
    n = len(closes)
    df = pd.DataFrame({"open": closes, "high": [c+10 for c in closes],
        "low": [c-10 for c in closes], "close": closes, "volume": [100.0]*n})
    return BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)

class TestRSI:
    def test_rsi_range(self):
        np.random.seed(42)
        closes = list(np.cumsum(np.random.randn(100)) + 100)
        result = rsi(_make_series(closes), period=14)
        assert isinstance(result, RSIResult)
        valid = result.values[~np.isnan(result.values)]
        assert np.all(valid >= 0) and np.all(valid <= 100)

    def test_rsi_overbought_on_rising(self):
        closes = [100.0 + i*5.0 for i in range(30)]
        assert rsi(_make_series(closes), period=14).values[-1] > 70.0

    def test_rsi_oversold_on_falling(self):
        closes = [200.0 - i*5.0 for i in range(30)]
        assert rsi(_make_series(closes), period=14).values[-1] < 30.0

    def test_rsi_length(self):
        assert len(rsi(_make_series([100.0]*30), period=14).values) == 30

    def test_rsi_warmup_nan(self):
        assert np.isnan(rsi(_make_series([100+i for i in range(20)]), period=14).values[0])

class TestMACD:
    def test_macd_basic(self):
        np.random.seed(42)
        result = macd(_make_series(list(np.cumsum(np.random.randn(50))+100)), fast=12, slow=26, signal=9)
        assert isinstance(result, MACDResult)
        assert len(result.macd_line) == 50
        assert len(result.signal_line) == 50
        assert len(result.histogram) == 50

    def test_macd_histogram_is_diff(self):
        np.random.seed(42)
        result = macd(_make_series(list(np.cumsum(np.random.randn(50))+100)))
        valid = ~np.isnan(result.histogram)
        np.testing.assert_array_almost_equal(result.histogram[valid],
            (result.macd_line - result.signal_line)[valid])

    def test_macd_length(self):
        assert len(macd(_make_series([100.0]*50)).macd_line) == 50

    def test_macd_warmup_nan(self):
        assert np.isnan(macd(_make_series([100+i for i in range(40)])).macd_line[0])
