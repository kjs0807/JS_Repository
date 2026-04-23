"""core/types.py 단위 테스트."""
import pandas as pd
import numpy as np
from src.core.types import Bar, BarSeries, ProductInfo


class TestBar:
    def test_create_bar(self):
        bar = Bar(
            symbol="BTCUSDT", timestamp=1700000000000, timeframe="1h",
            open=40000.0, high=40150.0, low=39900.0,
            close=40050.0, volume=1000.0,
        )
        assert bar.symbol == "BTCUSDT"
        assert bar.timestamp == 1700000000000
        assert bar.timeframe == "1h"
        assert bar.close == 40050.0
        assert bar.turnover is None

    def test_bar_is_frozen(self):
        bar = Bar(
            symbol="BTCUSDT", timestamp=1700000000000, timeframe="1h",
            open=40000.0, high=40150.0, low=39900.0,
            close=40050.0, volume=1000.0,
        )
        try:
            bar.close = 50000.0
            assert False, "frozen dataclass는 수정 불가해야 한다"
        except AttributeError:
            pass

    def test_bar_with_turnover(self):
        bar = Bar(
            symbol="ETHUSDT", timestamp=1700000000000, timeframe="15m",
            open=2000.0, high=2010.0, low=1990.0,
            close=2005.0, volume=500.0, turnover=1002500.0,
        )
        assert bar.turnover == 1002500.0


class TestBarSeries:
    def test_create_bar_series(self):
        df = pd.DataFrame({
            "open": [40000.0, 40100.0], "high": [40150.0, 40200.0],
            "low": [39900.0, 39950.0], "close": [40050.0, 40150.0],
            "volume": [1000.0, 1100.0],
        })
        series = BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)
        assert series.symbol == "BTCUSDT"
        assert len(series.bars) == 2

    def test_close_property(self):
        df = pd.DataFrame({
            "open": [100.0, 200.0, 300.0], "high": [110.0, 210.0, 310.0],
            "low": [90.0, 190.0, 290.0], "close": [105.0, 205.0, 305.0],
            "volume": [10.0, 20.0, 30.0],
        })
        series = BarSeries(symbol="ETHUSDT", timeframe="15m", bars=df)
        np.testing.assert_array_equal(series.close.values, [105.0, 205.0, 305.0])

    def test_high_low_properties(self):
        df = pd.DataFrame({
            "open": [100.0], "high": [110.0], "low": [90.0],
            "close": [105.0], "volume": [10.0],
        })
        series = BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)
        assert series.high.iloc[0] == 110.0
        assert series.low.iloc[0] == 90.0

    def test_length(self):
        df = pd.DataFrame({
            "open": [1.0, 2.0, 3.0], "high": [1.1, 2.1, 3.1],
            "low": [0.9, 1.9, 2.9], "close": [1.05, 2.05, 3.05],
            "volume": [10.0, 20.0, 30.0],
        })
        series = BarSeries(symbol="BTCUSDT", timeframe="4h", bars=df)
        assert len(series) == 3


class TestProductInfo:
    def test_create_product_info(self):
        product = ProductInfo(
            symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT",
            min_qty=0.001, qty_step=0.001, tick_size=0.1,
            min_notional=5.0, max_leverage=100,
        )
        assert product.symbol == "BTCUSDT"
        assert product.max_leverage == 100

    def test_product_info_defaults(self):
        product = ProductInfo(symbol="ETHUSDT", base_coin="ETH")
        assert product.quote_coin == "USDT"
        assert product.max_leverage is None
