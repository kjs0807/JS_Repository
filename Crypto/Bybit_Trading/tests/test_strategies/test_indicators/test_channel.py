"""indicators/channel.py 단위 테스트."""
import numpy as np
import pandas as pd
from src.core.types import BarSeries
from src.strategies.indicators.channel import donchian, DonchianResult

def _make_ohlc_series(highs, lows, closes=None):
    n = len(highs)
    if closes is None:
        closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    df = pd.DataFrame({
        "open": closes, "high": highs, "low": lows,
        "close": closes, "volume": [100.0] * n,
    })
    return BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)

class TestDonchian:
    def test_donchian_basic(self):
        highs = [100, 110, 105, 115, 108, 120, 118, 125]
        lows = [95, 100, 98, 105, 100, 110, 108, 115]
        result = donchian(_make_ohlc_series(highs, lows), period=3)
        assert isinstance(result, DonchianResult)
        assert len(result.upper) == 8 and len(result.lower) == 8 and len(result.middle) == 8

    def test_donchian_upper_is_max_of_prior_bars(self):
        highs = [100, 110, 105, 115, 108]
        lows = [90, 100, 95, 105, 98]
        result = donchian(_make_ohlc_series(highs, lows), period=3)
        assert result.upper[3] == 110  # max(high[0:3]) = max(100,110,105)
        assert result.upper[4] == 115  # max(high[1:4]) = max(110,105,115)

    def test_donchian_lower_is_min_of_prior_bars(self):
        highs = [100, 110, 105, 115, 108]
        lows = [90, 100, 95, 105, 98]
        result = donchian(_make_ohlc_series(highs, lows), period=3)
        assert result.lower[3] == 90  # min(low[0:3]) = min(90,100,95)
        assert result.lower[4] == 95  # min(low[1:4]) = min(100,95,105)

    def test_donchian_middle(self):
        highs = [100, 110, 105, 115]
        lows = [90, 100, 95, 105]
        result = donchian(_make_ohlc_series(highs, lows), period=3)
        assert result.middle[3] == 100  # (110 + 90) / 2

    def test_donchian_warmup_nan(self):
        highs = [100, 110, 105, 115, 108]
        lows = [90, 100, 95, 105, 98]
        result = donchian(_make_ohlc_series(highs, lows), period=3)
        assert np.isnan(result.upper[0])
        assert np.isnan(result.upper[1])
        assert np.isnan(result.upper[2])
        assert not np.isnan(result.upper[3])

    def test_donchian_excludes_current_bar(self):
        highs = [10, 20, 30, 40, 100]
        lows = [5, 15, 25, 35, 90]
        result = donchian(_make_ohlc_series(highs, lows), period=3)
        assert result.upper[4] == 40  # max(high[1:4]) = max(20,30,40) = 40 (현재 봉 100 제외)

    def test_donchian_length_matches_input(self):
        highs = list(range(100, 120))
        lows = list(range(90, 110))
        result = donchian(_make_ohlc_series(highs, lows), period=5)
        assert len(result.upper) == 20 and len(result.lower) == 20 and len(result.middle) == 20
