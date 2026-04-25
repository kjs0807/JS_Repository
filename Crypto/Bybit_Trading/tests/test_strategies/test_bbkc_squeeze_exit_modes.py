"""BBKCSqueeze exit_mode extension tests."""
import numpy as np
import pandas as pd
import pytest

from src.core.types import Bar, BarSeries
from src.execution.broker import Position
from src.strategies.bbkc_squeeze import BBKCSqueeze


class _MockBroker:
    def __init__(self):
        self.buys = []
        self.sells = []
        self.closes = []
        self.stop_updates = []
        self.positions = {}

    def buy(self, symbol, qty, stop_loss, take_profit=None, reason=""):
        self.buys.append((symbol, qty, stop_loss, take_profit, reason))
        return "buy"

    def sell(self, symbol, qty, stop_loss, take_profit=None, reason=""):
        self.sells.append((symbol, qty, stop_loss, take_profit, reason))
        return "sell"

    def close(self, symbol, reason=""):
        self.closes.append((symbol, reason))
        return "close"

    def update_stop(self, symbol, new_stop):
        self.stop_updates.append((symbol, new_stop))

    def get_position(self, symbol):
        return self.positions.get(symbol)

    def calc_qty(self, symbol, risk_pct, stop_distance):
        return 1.0


def _bars(closes):
    n = len(closes)
    df = pd.DataFrame({
        "open": closes,
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    })
    return BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)


def _stub_cache_with_position(s):
    """Build a cache long enough that on_bar_fast can run; values irrelevant when pos exists."""
    closes = [100.0] * 60
    series = _bars(closes)
    return s.prepare(series)


def test_default_params_preserve_fixed_mode():
    s = BBKCSqueeze()
    p = s.get_params()
    assert p["exit_mode"] == "fixed"
    assert p["trail_be_r"] == 1.0
    assert p["trail_start_r"] == 2.0
    assert p["trail_distance_r"] == 0.5
    assert p["time_stop_bars"] == 0


def test_set_params_updates_exit_mode():
    s = BBKCSqueeze()
    s.set_params({"exit_mode": "be_trail", "time_stop_bars": 48})
    assert s.exit_mode == "be_trail"
    assert s.time_stop_bars == 48


def test_invalid_exit_mode_rejected():
    with pytest.raises((ValueError, AssertionError)):
        BBKCSqueeze(exit_mode="bogus")


# ── Task 4: lazy _pos_meta init/cleanup ────────────────────────────────────


def test_pos_meta_lazy_init_when_position_appears():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    assert "BTCUSDT" not in s._pos_meta

    # LONG position with entry 100, SL 95 → R = 5
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    bar = Bar("BTCUSDT", 1700000000000, "1h", 100, 101, 99, 100, 1000)
    s.on_bar_fast(bar, 50, cache, broker)

    assert "BTCUSDT" in s._pos_meta
    meta = s._pos_meta["BTCUSDT"]
    assert meta["R"] == pytest.approx(5.0)
    assert meta["initial_sl"] == pytest.approx(95.0)
    assert meta["be_triggered"] is False
    assert meta["trail_active"] is False
    assert meta["bars_held"] == 1


def test_pos_meta_cleanup_when_position_disappears():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)

    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    bar = Bar("BTCUSDT", 1700000000000, "1h", 100, 101, 99, 100, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert "BTCUSDT" in s._pos_meta

    # Position closed externally (broker no longer has it)
    del broker.positions["BTCUSDT"]
    s.on_bar_fast(bar, 51, cache, broker)
    assert "BTCUSDT" not in s._pos_meta


def test_short_pos_meta_R_calculation():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)

    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "SHORT", 1.0, 100.0, 1700000000000,
        105.0, 90.0, 0.0, "BBKCSqueeze", 0.0,
    )
    bar = Bar("BTCUSDT", 1700000000000, "1h", 100, 101, 99, 100, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    meta = s._pos_meta["BTCUSDT"]
    # SHORT: R = SL - entry = 105 - 100 = 5
    assert meta["R"] == pytest.approx(5.0)
