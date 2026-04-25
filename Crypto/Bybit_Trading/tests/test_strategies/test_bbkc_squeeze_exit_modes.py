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


# ── Task 5: be_trail BE step (+1R → SL=entry) ─────────────────────────────


def test_be_trail_long_below_1R_no_change():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    # close=104 → +4 < 1R(=5) → no BE
    bar = Bar("BTCUSDT", 1700000000000, "1h", 104, 104, 104, 104, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert broker.stop_updates == []
    assert s._pos_meta["BTCUSDT"]["be_triggered"] is False


def test_be_trail_long_at_1R_triggers_BE():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    # close=105 → +5 >= 1R → BE: stop = entry = 100
    bar = Bar("BTCUSDT", 1700000000000, "1h", 105, 105, 105, 105, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert broker.stop_updates == [("BTCUSDT", 100.0)]
    assert s._pos_meta["BTCUSDT"]["be_triggered"] is True


def test_be_trail_long_BE_only_triggers_once():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    bar = Bar("BTCUSDT", 1700000000000, "1h", 105, 105, 105, 105, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    # Second bar still above 1R but below 2R — should NOT re-trigger BE
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 106, 106, 106, 106, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert len(broker.stop_updates) == 1


def test_be_trail_fixed_mode_does_not_BE():
    s = BBKCSqueeze(exit_mode="fixed")  # NOT be_trail
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    bar = Bar("BTCUSDT", 1700000000000, "1h", 110, 110, 110, 110, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert broker.stop_updates == []   # fixed never updates stop


# ── Task 6: be_trail trailing step (+2R → trail SL) ────────────────────────


def test_be_trail_long_at_2R_activates_trailing_with_first_sl():
    s = BBKCSqueeze(exit_mode="be_trail", trail_distance_r=0.5)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 130.0, 0.0, "BBKCSqueeze", 0.0,
    )
    # close=110 → +10 == 2R(=10) → BE + trailing activated
    # trailing SL = close - 0.5*R = 110 - 2.5 = 107.5
    bar = Bar("BTCUSDT", 1700000000000, "1h", 110, 110, 110, 110, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert ("BTCUSDT", 100.0) in broker.stop_updates
    assert ("BTCUSDT", 107.5) in broker.stop_updates
    assert s._pos_meta["BTCUSDT"]["trail_active"] is True


def test_be_trail_long_trailing_only_ratchets_up():
    s = BBKCSqueeze(exit_mode="be_trail", trail_distance_r=0.5)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 130.0, 0.0, "BBKCSqueeze", 0.0,
    )
    # First bar: close=112 → +12 >= 2R → trail activated, SL = 112 - 2.5 = 109.5
    bar1 = Bar("BTCUSDT", 1700000000000, "1h", 112, 112, 112, 112, 1000)
    s.on_bar_fast(bar1, 50, cache, broker)
    # Simulate broker now reflects the trailed stop
    broker.positions["BTCUSDT"].stop_loss = 109.5

    # Second bar: close=111 (lower) → would compute SL=108.5, but ratchet says no
    broker.stop_updates.clear()
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 111, 111, 111, 111, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert broker.stop_updates == []

    # Third bar: close=115 → SL=112.5, higher than 109.5 → ratchet up
    bar3 = Bar("BTCUSDT", 1700000000002, "1h", 115, 115, 115, 115, 1000)
    s.on_bar_fast(bar3, 52, cache, broker)
    assert broker.stop_updates == [("BTCUSDT", 112.5)]


def test_be_trail_short_symmetry_at_2R():
    s = BBKCSqueeze(exit_mode="be_trail", trail_distance_r=0.5)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "SHORT", 1.0, 100.0, 1700000000000,
        105.0, 70.0, 0.0, "BBKCSqueeze", 0.0,
    )
    # SHORT R=5. close=90 → move=10 >= 2R → BE + trailing
    # trailing SL = close + 0.5*R = 90 + 2.5 = 92.5
    bar = Bar("BTCUSDT", 1700000000000, "1h", 90, 90, 90, 90, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert ("BTCUSDT", 100.0) in broker.stop_updates       # BE
    assert ("BTCUSDT", 92.5) in broker.stop_updates        # trail


# ── Task 7: time_stop fallback ──────────────────────────────────────────────


def test_time_stop_zero_does_nothing():
    s = BBKCSqueeze(exit_mode="fixed", time_stop_bars=0)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    for k in range(100):
        bar = Bar("BTCUSDT", 1700000000000 + k, "1h", 100, 100, 100, 100, 1000)
        s.on_bar_fast(bar, 50 + k, cache, broker)
    assert broker.closes == []


def test_time_stop_triggers_at_N_bars_held():
    s = BBKCSqueeze(exit_mode="fixed", time_stop_bars=3)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    # bars_held increments to 1, 2, 3 → at 3 should fire close
    for k in range(3):
        bar = Bar("BTCUSDT", 1700000000000 + k, "1h", 100, 100, 100, 100, 1000)
        s.on_bar_fast(bar, 50 + k, cache, broker)
    assert broker.closes == [("BTCUSDT", "time_stop")]


def test_time_stop_works_with_be_trail():
    s = BBKCSqueeze(exit_mode="be_trail", time_stop_bars=2)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    # Below 1R both bars → no BE, just bars_held increments
    bar1 = Bar("BTCUSDT", 1700000000000, "1h", 102, 102, 102, 102, 1000)
    s.on_bar_fast(bar1, 50, cache, broker)
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 103, 103, 103, 103, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert broker.closes == [("BTCUSDT", "time_stop")]


def test_time_stop_skipped_if_position_already_gone():
    s = BBKCSqueeze(exit_mode="fixed", time_stop_bars=2)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    bar1 = Bar("BTCUSDT", 1700000000000, "1h", 100, 100, 100, 100, 1000)
    s.on_bar_fast(bar1, 50, cache, broker)
    # SL hit externally → broker removes position
    del broker.positions["BTCUSDT"]
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 100, 100, 100, 100, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert broker.closes == []
