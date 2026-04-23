"""Donchian_TrendFilter 전략 테스트."""
import pandas as pd
import numpy as np
from src.core.types import Bar, BarSeries
from src.strategies.donchian_trend_filter import DonchianTrendFilter

class MockBroker:
    def __init__(self):
        self.buys = []
        self.sells = []
        self.closes = []
        self.positions = {}
    def buy(self, symbol, qty, stop_loss, take_profit=None, reason=""):
        self.buys.append((symbol, qty, stop_loss, reason))
        return "mock_buy"
    def sell(self, symbol, qty, stop_loss, take_profit=None, reason=""):
        self.sells.append((symbol, qty, stop_loss, reason))
        return "mock_sell"
    def close(self, symbol, reason=""):
        self.closes.append((symbol, reason))
        return "mock_close"
    def get_position(self, symbol):
        return self.positions.get(symbol)
    def calc_qty(self, symbol, risk_pct, stop_distance):
        return 1.0

def _make_bars(closes, highs=None, lows=None):
    n = len(closes)
    if highs is None: highs = [c + 1 for c in closes]
    if lows is None: lows = [c - 1 for c in closes]
    df = pd.DataFrame({"open": closes, "high": highs, "low": lows,
                       "close": closes, "volume": [1000.0] * n})
    return BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)

class TestDonchianTrendFilter:
    def test_strategy_has_protocol_fields(self):
        s = DonchianTrendFilter()
        assert s.name == "Donchian_TrendFilter"
        assert s.timeframe in ("1h", "4h")
        assert isinstance(s.warmup_bars, int)

    def test_warmup_respects_longest_period(self):
        s = DonchianTrendFilter(entry_period=20, exit_period=10, ema_filter=200, atr_period=14)
        assert s.warmup_bars >= 200

    def test_no_signal_during_warmup(self):
        s = DonchianTrendFilter(entry_period=3, ema_filter=5, atr_period=3)
        broker = MockBroker()
        closes = [100, 101, 102]
        series = _make_bars(closes)
        bar = Bar("BTCUSDT", 1700000000000, "1h", 102, 103, 101, 102, 1000)
        s.on_bar(bar, series, broker)
        assert len(broker.buys) == 0 and len(broker.sells) == 0

    def test_long_signal_on_breakout_with_uptrend(self):
        s = DonchianTrendFilter(entry_period=5, exit_period=5, ema_filter=10, atr_period=5)
        broker = MockBroker()
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                  110, 111, 112, 113, 114, 115, 116, 117, 118, 130]
        series = _make_bars(closes)
        bar = Bar("BTCUSDT", 1700000000000, "1h", 119, 131, 118, 130, 1000)
        s.on_bar(bar, series, broker)
        assert len(broker.buys) == 1

    def test_no_signal_when_position_exists(self):
        s = DonchianTrendFilter(entry_period=5, exit_period=5, ema_filter=10, atr_period=5)
        broker = MockBroker()
        from src.execution.broker import Position
        broker.positions["BTCUSDT"] = Position("BTCUSDT", "LONG", 0.01, 100.0, 1700000000000,
                                                95.0, 110.0, 0.0, "Donchian_TrendFilter")
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                  110, 111, 112, 113, 114, 115, 116, 117, 118, 130]
        series = _make_bars(closes)
        bar = Bar("BTCUSDT", 1700000000000, "1h", 119, 131, 118, 130, 1000)
        s.on_bar(bar, series, broker)
        assert len(broker.buys) == 0

    def test_close_on_opposite_channel_break(self):
        s = DonchianTrendFilter(entry_period=5, exit_period=5, ema_filter=10, atr_period=5)
        broker = MockBroker()
        from src.execution.broker import Position
        broker.positions["BTCUSDT"] = Position("BTCUSDT", "LONG", 0.01, 120.0, 1700000000000,
                                                95.0, None, 0.0, "Donchian_TrendFilter")
        closes = [130, 128, 125, 122, 118, 115, 112, 110, 108, 105,
                  102, 100, 98, 95, 93, 91, 89, 87, 85, 70]
        series = _make_bars(closes)
        bar = Bar("BTCUSDT", 1700000000000, "1h", 85, 86, 69, 70, 1000)
        s.on_bar(bar, series, broker)
        assert len(broker.closes) == 1

    def test_get_set_params(self):
        s = DonchianTrendFilter()
        params = s.get_params()
        assert "entry_period" in params and "exit_period" in params
        assert "ema_filter" in params and "stop_atr" in params
        s.set_params({"entry_period": 55, "stop_atr": 3.0})
        assert s.entry_period == 55 and s.stop_atr == 3.0


import numpy as np
from src.strategies.base import IndicatorCache


class TestDonchianTrendFilterFastPath:
    def test_prepare_returns_cache(self):
        s = DonchianTrendFilter(entry_period=5, exit_period=5, ema_filter=10, atr_period=5)
        closes = [100 + i for i in range(30)]
        series = _make_bars(closes)
        cache = s.prepare(series)
        assert isinstance(cache, IndicatorCache)
        assert "upper_entry" in cache.arrays
        assert "lower_entry" in cache.arrays
        assert "upper_exit" in cache.arrays
        assert "lower_exit" in cache.arrays
        assert "ema" in cache.arrays
        assert "atr" in cache.arrays

    def test_cache_arrays_length_matches_series(self):
        s = DonchianTrendFilter(entry_period=5, exit_period=5, ema_filter=10, atr_period=5)
        closes = [100 + i for i in range(30)]
        series = _make_bars(closes)
        cache = s.prepare(series)
        for key, arr in cache.arrays.items():
            assert len(arr) == 30, f"{key} length mismatch"

    def test_on_bar_fast_long_signal(self):
        s = DonchianTrendFilter(entry_period=5, exit_period=5, ema_filter=10, atr_period=5)
        broker = MockBroker()
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                  110, 111, 112, 113, 114, 115, 116, 117, 118, 130]
        series = _make_bars(closes)
        cache = s.prepare(series)
        last_bar = Bar("BTCUSDT", 1700000000000, "1h", 119, 131, 118, 130, 1000)
        s.on_bar_fast(last_bar, 19, cache, broker)
        assert len(broker.buys) == 1

    def test_on_bar_fast_no_signal_when_position(self):
        s = DonchianTrendFilter(entry_period=5, exit_period=5, ema_filter=10, atr_period=5)
        broker = MockBroker()
        from src.execution.broker import Position
        broker.positions["BTCUSDT"] = Position("BTCUSDT", "LONG", 0.01, 100.0,
                                                1700000000000, 95.0, 110.0, 0.0, "Donchian_TrendFilter")
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                  110, 111, 112, 113, 114, 115, 116, 117, 118, 130]
        series = _make_bars(closes)
        cache = s.prepare(series)
        last_bar = Bar("BTCUSDT", 1700000000000, "1h", 119, 131, 118, 130, 1000)
        s.on_bar_fast(last_bar, 19, cache, broker)
        assert len(broker.buys) == 0

    def test_on_bar_fast_nan_skip(self):
        s = DonchianTrendFilter(entry_period=5, exit_period=5, ema_filter=10, atr_period=5)
        broker = MockBroker()
        closes = [100] * 30
        series = _make_bars(closes)
        cache = s.prepare(series)
        bar = Bar("BTCUSDT", 1700000000000, "1h", 100, 101, 99, 100, 1000)
        s.on_bar_fast(bar, 0, cache, broker)
        assert len(broker.buys) == 0
        assert len(broker.sells) == 0
