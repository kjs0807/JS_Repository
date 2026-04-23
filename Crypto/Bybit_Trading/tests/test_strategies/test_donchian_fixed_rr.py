"""Donchian_FixedRR 전략 테스트."""
import pandas as pd
import numpy as np
from src.core.types import Bar, BarSeries
from src.strategies.donchian_fixed_rr import DonchianFixedRR

class MockBroker:
    def __init__(self):
        self.buys = []
        self.sells = []
        self.positions = {}
    def buy(self, symbol, qty, stop_loss, take_profit=None, reason=""):
        self.buys.append((symbol, qty, stop_loss, take_profit, reason))
        return "mock_buy"
    def sell(self, symbol, qty, stop_loss, take_profit=None, reason=""):
        self.sells.append((symbol, qty, stop_loss, take_profit, reason))
        return "mock_sell"
    def close(self, symbol, reason=""):
        return "mock_close"
    def get_position(self, symbol):
        return self.positions.get(symbol)
    def calc_qty(self, symbol, risk_pct, stop_distance):
        return 1.0
    def update_stop(self, symbol, new_stop):
        pos = self.positions.get(symbol)
        if pos:
            pos.stop_loss = new_stop

def _make_bars(closes, highs=None, lows=None):
    n = len(closes)
    if highs is None: highs = [c + 1 for c in closes]
    if lows is None: lows = [c - 1 for c in closes]
    df = pd.DataFrame({"open": closes, "high": highs, "low": lows,
                       "close": closes, "volume": [1000.0] * n})
    return BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)

class TestDonchianFixedRR:
    def test_strategy_has_protocol_fields(self):
        assert DonchianFixedRR().name == "Donchian_FixedRR"

    def test_long_signal_sets_tp_and_sl(self):
        s = DonchianFixedRR(entry_period=5, atr_period=5, stop_atr=2.0, tp_r_ratio=2.0)
        broker = MockBroker()
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 130]
        series = _make_bars(closes)
        bar = Bar("BTCUSDT", 1700000000000, "1h", 109, 131, 108, 130, 1000)
        s.on_bar(bar, series, broker)
        assert len(broker.buys) == 1
        _, _, sl, tp, _ = broker.buys[0]
        assert sl < 130 and tp > 130
        rr = (tp - 130) / (130 - sl)
        assert abs(rr - 2.0) < 0.1

    def test_short_signal_sets_tp_and_sl(self):
        s = DonchianFixedRR(entry_period=5, atr_period=5, stop_atr=2.0, tp_r_ratio=2.0)
        broker = MockBroker()
        closes = [130, 128, 125, 122, 118, 115, 112, 110, 108, 80]
        series = _make_bars(closes)
        bar = Bar("BTCUSDT", 1700000000000, "1h", 108, 109, 79, 80, 1000)
        s.on_bar(bar, series, broker)
        assert len(broker.sells) == 1
        _, _, sl, tp, _ = broker.sells[0]
        assert sl > 80 and tp < 80

    def test_no_signal_when_position_exists(self):
        s = DonchianFixedRR(entry_period=5, atr_period=5)
        broker = MockBroker()
        from src.execution.broker import Position
        broker.positions["BTCUSDT"] = Position("BTCUSDT", "LONG", 0.01, 100.0, 1700000000000,
                                                95.0, 110.0, 0.0, "Donchian_FixedRR")
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 130]
        series = _make_bars(closes)
        bar = Bar("BTCUSDT", 1700000000000, "1h", 109, 131, 108, 130, 1000)
        s.on_bar(bar, series, broker)
        assert len(broker.buys) == 0

    def test_trailing_stop_activates_on_profit(self):
        s = DonchianFixedRR(entry_period=5, atr_period=5, stop_atr=2.0, tp_r_ratio=4.0,
                            trail_activate_atr=1.5, trail_distance_atr=1.0)
        broker = MockBroker()
        from src.execution.broker import Position
        broker.positions["BTCUSDT"] = Position("BTCUSDT", "LONG", 0.01, 100.0, 1700000000000,
                                                95.0, 120.0, 0.0, "Donchian_FixedRR")
        closes = [100]*9 + [110]
        highs = [105]*9 + [112]
        lows = [95]*9 + [108]
        series = _make_bars(closes, highs, lows)
        bar = Bar("BTCUSDT", 1700000000000, "1h", 109, 112, 108, 110, 1000)
        s.on_bar(bar, series, broker)
        pos = broker.positions["BTCUSDT"]
        assert pos.stop_loss >= 95.0

    def test_get_set_params(self):
        s = DonchianFixedRR()
        params = s.get_params()
        assert "entry_period" in params and "stop_atr" in params and "tp_r_ratio" in params
        s.set_params({"entry_period": 55, "tp_r_ratio": 3.0})
        assert s.entry_period == 55 and s.tp_r_ratio == 3.0


import numpy as np
from src.strategies.base import IndicatorCache


class TestDonchianFixedRRFastPath:
    def test_prepare_returns_cache(self):
        s = DonchianFixedRR(entry_period=5, atr_period=5, stop_atr=2.0, tp_r_ratio=2.0)
        closes = [100 + i for i in range(30)]
        series = _make_bars(closes)
        cache = s.prepare(series)
        assert isinstance(cache, IndicatorCache)
        assert "upper" in cache.arrays
        assert "lower" in cache.arrays
        assert "atr" in cache.arrays

    def test_on_bar_fast_long_signal_with_tp_sl(self):
        s = DonchianFixedRR(entry_period=5, atr_period=5, stop_atr=2.0, tp_r_ratio=2.0)
        broker = MockBroker()
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 130]
        series = _make_bars(closes)
        cache = s.prepare(series)
        bar = Bar("BTCUSDT", 1700000000000, "1h", 109, 131, 108, 130, 1000)
        s.on_bar_fast(bar, 9, cache, broker)
        assert len(broker.buys) == 1
        _, _, sl, tp, _ = broker.buys[0]
        assert sl < 130 and tp > 130
        rr = (tp - 130) / (130 - sl)
        assert abs(rr - 2.0) < 0.1

    def test_on_bar_fast_trailing_stop_activates(self):
        s = DonchianFixedRR(entry_period=5, atr_period=5, stop_atr=2.0, tp_r_ratio=4.0,
                            trail_activate_atr=1.5, trail_distance_atr=1.0)
        broker = MockBroker()
        from src.execution.broker import Position
        broker.positions["BTCUSDT"] = Position("BTCUSDT", "LONG", 0.01, 100.0,
                                                1700000000000, 95.0, 120.0, 0.0, "Donchian_FixedRR")
        closes = [100]*9 + [110]
        highs = [105]*9 + [112]
        lows = [95]*9 + [108]
        series = _make_bars(closes, highs, lows)
        cache = s.prepare(series)
        bar = Bar("BTCUSDT", 1700000000000, "1h", 109, 112, 108, 110, 1000)
        s.on_bar_fast(bar, 9, cache, broker)
        pos = broker.positions["BTCUSDT"]
        assert pos.stop_loss >= 95.0
