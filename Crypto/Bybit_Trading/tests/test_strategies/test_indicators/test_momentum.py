"""indicators/momentum.py 단위 테스트."""
import numpy as np
import pandas as pd
from src.core.types import BarSeries
from src.strategies.indicators.momentum import (
    atr, adx, bollinger, keltner, ATRResult, ADXResult, BollingerResult, KeltnerResult)

def _make_ohlcv_series(n=50):
    np.random.seed(42)
    base = 100.0
    o, h, l, c = [], [], [], []
    for _ in range(n):
        cl = base + np.random.randn()*2
        op = cl + np.random.randn()*0.5
        hi = max(op,cl) + abs(np.random.randn())
        lo = min(op,cl) - abs(np.random.randn())
        o.append(op); h.append(hi); l.append(lo); c.append(cl)
        base = cl
    df = pd.DataFrame({"open":o,"high":h,"low":l,"close":c,"volume":[1000]*n})
    return BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)

class TestATR:
    def test_atr_positive(self):
        result = atr(_make_ohlcv_series(50), period=14)
        assert isinstance(result, ATRResult)
        valid = result.values[~np.isnan(result.values)]
        assert len(valid) > 0 and np.all(valid > 0)

    def test_atr_length(self):
        assert len(atr(_make_ohlcv_series(50), period=14).values) == 50

    def test_atr_warmup_nan(self):
        assert np.isnan(atr(_make_ohlcv_series(20), period=14).values[0])

class TestADX:
    def test_adx_range(self):
        result = adx(_make_ohlcv_series(60), period=14)
        assert isinstance(result, ADXResult)
        valid = result.values[~np.isnan(result.values)]
        assert len(valid) > 0 and np.all(valid >= 0) and np.all(valid <= 100)

    def test_adx_length(self):
        assert len(adx(_make_ohlcv_series(60), period=14).values) == 60

class TestBollinger:
    def test_bollinger_bands(self):
        result = bollinger(_make_ohlcv_series(30), period=20, std=2.0)
        assert isinstance(result, BollingerResult)
        assert len(result.upper) == 30 and len(result.mid) == 30 and len(result.lower) == 30

    def test_upper_gt_mid_gt_lower(self):
        result = bollinger(_make_ohlcv_series(30), period=20, std=2.0)
        m = ~np.isnan(result.upper)
        assert np.all(result.upper[m] >= result.mid[m]) and np.all(result.mid[m] >= result.lower[m])

    def test_bandwidth(self):
        result = bollinger(_make_ohlcv_series(30), period=20, std=2.0)
        valid = result.bandwidth[~np.isnan(result.bandwidth)]
        assert np.all(valid >= 0)

class TestKeltner:
    def test_keltner_channel(self):
        result = keltner(_make_ohlcv_series(30), ema_period=20, atr_period=14, atr_mult=1.5)
        assert isinstance(result, KeltnerResult)
        assert len(result.upper) == 30

    def test_upper_gt_mid_gt_lower(self):
        result = keltner(_make_ohlcv_series(30), ema_period=20, atr_period=14, atr_mult=1.5)
        m = ~np.isnan(result.upper)
        assert np.all(result.upper[m] >= result.mid[m]) and np.all(result.mid[m] >= result.lower[m])

    def test_squeeze_detection(self):
        s = _make_ohlcv_series(30)
        k = keltner(s, ema_period=20, atr_period=14, atr_mult=1.5)
        b = bollinger(s, period=20, std=2.0)
        assert len(k.upper) == len(b.upper)
