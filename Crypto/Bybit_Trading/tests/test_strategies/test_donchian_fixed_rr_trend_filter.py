"""DonchianFixedRRTrendFilter tests.

Focus: the EMA(ema_filter) trend gate correctly blocks entries against
the trend while letting entries aligned with the trend through. The
underlying ATR stop / fixed RR TP / trailing logic is inherited from
DonchianFixedRR (already tested in test_donchian_fixed_rr.py), so we
only exercise the new gate branch plus a baseline regression call.
"""
from __future__ import annotations

import pandas as pd

from src.core.types import Bar, BarSeries
from src.strategies.donchian_fixed_rr_trend_filter import (
    DonchianFixedRRTrendFilter,
)


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
    if highs is None:
        highs = [c + 1 for c in closes]
    if lows is None:
        lows = [c - 1 for c in closes]
    df = pd.DataFrame({
        "open": closes, "high": highs, "low": lows,
        "close": closes, "volume": [1000.0] * n,
    })
    return BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)


class TestDonchianFixedRRTrendFilterBasics:
    def test_name_and_params(self):
        s = DonchianFixedRRTrendFilter()
        assert s.name == "Donchian_FixedRR_TrendFilter"
        assert s.ema_filter == 200
        params = s.get_params()
        assert "ema_filter" in params
        assert params["ema_filter"] == 200

    def test_warmup_covers_ema(self):
        s = DonchianFixedRRTrendFilter(ema_filter=50)
        assert s.warmup_bars >= 50 + 1


class TestDonchianFixedRRTrendFilterEntryGate:
    """Core of this variant: entry must respect EMA direction."""

    def test_long_entry_blocked_when_close_below_ema(self):
        """Long Donchian breakout present, but close is below EMA.
        Baseline FixedRR would fire; the trend filter must block it.

        Fixture: long history at 300 (pushes EMA(5) up to 300), then
        5 low bars at 100, then a breakout bar at 130. At the last
        bar: Donchian(5) upper = max(highs[50..54]) = 101, so 130 > 101
        (breakout). EMA(5) is still pulled toward the old 300 level and
        sits above 130 so close < ema -> gate blocks the long.
        """
        closes = [350.0] * 50 + [100.0] * 5 + [130.0]
        series = _make_bars(closes)
        s = DonchianFixedRRTrendFilter(
            entry_period=5, atr_period=5, stop_atr=2.0, tp_r_ratio=2.0,
            ema_filter=5,
        )
        broker = MockBroker()
        cache = s.prepare(series)

        i = len(closes) - 1
        upper_i = cache.arrays["upper"][i]
        ema_i = cache.arrays["ema"][i]
        # Sanity check that the fixture ACTUALLY sets up the gated case.
        assert closes[i] > upper_i, (
            f"fixture error: close={closes[i]} should break upper={upper_i}"
        )
        assert closes[i] < ema_i, (
            f"fixture error: close={closes[i]} should be below ema={ema_i}"
        )

        bar = Bar("BTCUSDT", 1700000000000, "1h",
                  closes[i], closes[i] + 1, closes[i] - 1, closes[i], 1000)
        s.on_bar_fast(bar, i, cache, broker)
        assert len(broker.buys) == 0, "EMA gate should block counter-trend long"
        assert len(broker.sells) == 0

    def test_long_entry_allowed_when_close_above_ema(self):
        """Long breakout in an uptrending EMA — gate lets it through."""
        # Classic FixedRR long-signal fixture from test_donchian_fixed_rr.py
        closes = [100.0, 101.0, 102.0, 103.0, 104.0,
                  105.0, 106.0, 107.0, 108.0, 130.0]
        series = _make_bars(closes)
        s = DonchianFixedRRTrendFilter(
            entry_period=5, atr_period=5, stop_atr=2.0, tp_r_ratio=2.0,
            ema_filter=5,
        )
        broker = MockBroker()
        cache = s.prepare(series)

        i = len(closes) - 1
        upper_i = cache.arrays["upper"][i]
        ema_i = cache.arrays["ema"][i]
        assert closes[i] > upper_i, "fixture should produce breakout"
        assert closes[i] > ema_i, "fixture should have close > ema"

        bar = Bar("BTCUSDT", 1700000000000, "1h",
                  closes[i], closes[i] + 1, closes[i] - 1, closes[i], 1000)
        s.on_bar_fast(bar, i, cache, broker)
        assert len(broker.buys) == 1
        _, _, sl, tp, _ = broker.buys[0]
        assert sl < 130 and tp > 130

    def test_short_entry_blocked_when_close_above_ema(self):
        """Short Donchian breakout present, but close is above EMA.
        Fixture mirrors the long-blocked case."""
        closes = [100.0] * 50 + [350.0] * 5 + [330.0]
        series = _make_bars(closes)
        s = DonchianFixedRRTrendFilter(
            entry_period=5, atr_period=5, stop_atr=2.0, tp_r_ratio=2.0,
            ema_filter=5,
        )
        broker = MockBroker()
        cache = s.prepare(series)
        i = len(closes) - 1

        lower_i = cache.arrays["lower"][i]
        ema_i = cache.arrays["ema"][i]
        assert closes[i] < lower_i, (
            f"fixture error: close={closes[i]} should break lower={lower_i}"
        )
        assert closes[i] > ema_i, (
            f"fixture error: close={closes[i]} should be above ema={ema_i}"
        )

        bar = Bar("BTCUSDT", 1700000000000, "1h",
                  closes[i], closes[i] + 1, closes[i] - 1, closes[i], 1000)
        s.on_bar_fast(bar, i, cache, broker)
        assert len(broker.sells) == 0, "EMA gate should block counter-trend short"
        assert len(broker.buys) == 0

    def test_short_entry_allowed_when_close_below_ema(self):
        """Short breakout in a downtrending EMA — gate lets it through."""
        # Classic FixedRR short-signal fixture from test_donchian_fixed_rr.py
        closes = [130.0, 128.0, 125.0, 122.0, 118.0,
                  115.0, 112.0, 110.0, 108.0, 80.0]
        series = _make_bars(closes)
        s = DonchianFixedRRTrendFilter(
            entry_period=5, atr_period=5, stop_atr=2.0, tp_r_ratio=2.0,
            ema_filter=5,
        )
        broker = MockBroker()
        cache = s.prepare(series)
        i = len(closes) - 1

        lower_i = cache.arrays["lower"][i]
        ema_i = cache.arrays["ema"][i]
        assert closes[i] < lower_i
        assert closes[i] < ema_i

        bar = Bar("BTCUSDT", 1700000000000, "1h",
                  closes[i], closes[i] + 1, closes[i] - 1, closes[i], 1000)
        s.on_bar_fast(bar, i, cache, broker)
        assert len(broker.sells) == 1
        _, _, sl, tp, _ = broker.sells[0]
        assert sl > 80 and tp < 80


class TestDonchianFixedRRTrendFilterBaselineRegression:
    """Sanity: parent's trailing / tp / sl / position lock unchanged."""

    def test_position_lock_skips_new_entry(self):
        from src.execution.broker import Position

        closes = [100.0, 101.0, 102.0, 103.0, 104.0,
                  105.0, 106.0, 107.0, 108.0, 130.0]
        series = _make_bars(closes)
        s = DonchianFixedRRTrendFilter(
            entry_period=5, atr_period=5, ema_filter=5,
        )
        broker = MockBroker()
        broker.positions["BTCUSDT"] = Position(
            "BTCUSDT", "LONG", 0.01, 100.0, 1700000000000,
            95.0, 110.0, 0.0, "Donchian_FixedRR_TrendFilter",
        )
        cache = s.prepare(series)
        i = len(closes) - 1
        bar = Bar("BTCUSDT", 1700000000000, "1h",
                  closes[i], closes[i] + 1, closes[i] - 1, closes[i], 1000)
        s.on_bar_fast(bar, i, cache, broker)
        assert len(broker.buys) == 0

    def test_on_bar_slow_path_matches_fast(self):
        closes = [100.0, 101.0, 102.0, 103.0, 104.0,
                  105.0, 106.0, 107.0, 108.0, 130.0]
        series = _make_bars(closes)
        s = DonchianFixedRRTrendFilter(
            entry_period=5, atr_period=5, ema_filter=5,
        )
        broker_fast = MockBroker()
        broker_slow = MockBroker()
        cache = s.prepare(series)
        i = len(closes) - 1
        bar = Bar("BTCUSDT", 1700000000000, "1h",
                  closes[i], closes[i] + 1, closes[i] - 1, closes[i], 1000)
        s.on_bar_fast(bar, i, cache, broker_fast)
        s.on_bar(bar, series, broker_slow)
        assert len(broker_fast.buys) == len(broker_slow.buys)
