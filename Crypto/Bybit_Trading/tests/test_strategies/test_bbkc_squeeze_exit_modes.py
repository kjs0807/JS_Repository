"""BBKCSqueeze exit_mode extension tests (round 3 — TP-fraction units)."""
import numpy as np
import pandas as pd
import pytest

from src.core.types import Bar, BarSeries
from src.execution.broker import Position
from src.strategies.bbkc_squeeze import BBKCSqueeze


def test_default_params_are_tp_fraction_units():
    s = BBKCSqueeze()
    p = s.get_params()
    assert p["exit_mode"] == "fixed"
    assert p["trail_be_at_tp_frac"] == 0.5
    assert p["trail_start_at_tp_frac"] == 0.8
    assert p["trail_distance_tp_frac"] == 0.3
    assert p["drop_tp"] is False
    assert p["time_stop_bars"] == 0
    assert "trail_be_r" not in p
    assert "trail_start_r" not in p
    assert "trail_distance_r" not in p


def test_set_params_updates_exit_mode():
    s = BBKCSqueeze()
    s.set_params({"exit_mode": "be_trail", "drop_tp": True, "time_stop_bars": 48})
    assert s.exit_mode == "be_trail"
    assert s.drop_tp is True
    assert s.time_stop_bars == 48


def test_invalid_exit_mode_rejected():
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="bogus")


def test_invariant_rejects_be_geq_start():
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="be_trail", trail_be_at_tp_frac=0.5,
                    trail_start_at_tp_frac=0.5)
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="be_trail", trail_be_at_tp_frac=0.7,
                    trail_start_at_tp_frac=0.5)


def test_invariant_rejects_out_of_unit_interval():
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="be_trail", trail_be_at_tp_frac=0.0,
                    trail_start_at_tp_frac=0.8)
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="be_trail", trail_be_at_tp_frac=0.5,
                    trail_start_at_tp_frac=1.0)


def test_invariant_rejects_distance_zero_or_negative():
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="be_trail", trail_distance_tp_frac=0.0)
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="be_trail", trail_distance_tp_frac=-0.1)


def test_invariant_accepts_immediate_cell():
    s = BBKCSqueeze(exit_mode="be_trail",
                    trail_be_at_tp_frac=0.49,
                    trail_start_at_tp_frac=0.50,
                    trail_distance_tp_frac=0.3)
    assert s.trail_be_at_tp_frac == 0.49
    assert s.trail_start_at_tp_frac == 0.50


def test_invariant_skipped_for_fixed_mode():
    s = BBKCSqueeze(exit_mode="fixed", trail_be_at_tp_frac=0.9,
                    trail_start_at_tp_frac=0.5)
    assert s.exit_mode == "fixed"


# ── set_params() invariant validation (optimizer / walk_forward 경로) ────


def test_set_params_rejects_invalid_be_geq_start():
    """set_params must run the same invariant as __init__."""
    s = BBKCSqueeze(exit_mode="be_trail")
    with pytest.raises(ValueError):
        s.set_params({"trail_be_at_tp_frac": 0.9, "trail_start_at_tp_frac": 0.5})


def test_set_params_rejects_invalid_distance():
    s = BBKCSqueeze(exit_mode="be_trail")
    with pytest.raises(ValueError):
        s.set_params({"trail_distance_tp_frac": 0.0})


def test_set_params_rejects_partial_invariant_violation():
    """Even when only one of be/start is set in dict, invariant uses current
    other attr — so be_at=0.9 with current start=0.8 must be rejected."""
    s = BBKCSqueeze(exit_mode="be_trail")  # default 0.5/0.8/0.3
    with pytest.raises(ValueError):
        s.set_params({"trail_be_at_tp_frac": 0.9})


def test_set_params_no_partial_mutation_on_failure():
    """If validation fails, no attribute should have changed."""
    s = BBKCSqueeze(exit_mode="be_trail",
                    trail_be_at_tp_frac=0.5,
                    trail_start_at_tp_frac=0.8,
                    trail_distance_tp_frac=0.3)
    # Mix of valid + invalid — should reject atomically
    with pytest.raises(ValueError):
        s.set_params({
            "rsi_filter": 65.0,           # valid, would normally apply
            "trail_be_at_tp_frac": 0.95,  # invalid (>= start=0.8)
        })
    # rsi_filter must NOT have been mutated
    assert s.rsi_filter == 70.0
    assert s.trail_be_at_tp_frac == 0.5


def test_set_params_accepts_valid_full_replacement():
    s = BBKCSqueeze(exit_mode="be_trail")
    s.set_params({
        "trail_be_at_tp_frac": 0.30,
        "trail_start_at_tp_frac": 0.60,
        "trail_distance_tp_frac": 0.20,
    })
    assert s.trail_be_at_tp_frac == 0.30
    assert s.trail_start_at_tp_frac == 0.60
    assert s.trail_distance_tp_frac == 0.20


def test_set_params_invariant_skipped_when_switching_to_fixed():
    """If exit_mode goes to 'fixed', trail invariants are not enforced."""
    s = BBKCSqueeze(exit_mode="be_trail")
    # Switch to fixed AND set invalid trail values in same call — must succeed
    s.set_params({
        "exit_mode": "fixed",
        "trail_be_at_tp_frac": 0.9,
        "trail_start_at_tp_frac": 0.5,
    })
    assert s.exit_mode == "fixed"
    assert s.trail_be_at_tp_frac == 0.9


# ── _pos_meta lazy init / cleanup (no R in meta) + be_trail TP-fraction ──


class _MockBroker:
    def __init__(self):
        self.buys = []
        self.sells = []
        self.closes = []
        self.stop_updates = []
        self.tp_updates = []
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

    def update_tp(self, symbol, new_tp):
        self.tp_updates.append((symbol, new_tp))

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


def _stub_cache(s):
    closes = [100.0] * 60
    series = _bars(closes)
    return s.prepare(series)


def _make_long_pos(entry=100.0, stop=97.67, tp=102.0):
    """Default LONG. tp_distance = entry × 0.06 / 3 = 2.0."""
    return Position(
        "BTCUSDT", "LONG", 1.0, entry, 1700000000000,
        stop, tp, 0.0, "BBKCSqueeze", 0.0,
    )


def _make_short_pos(entry=100.0, stop=102.33, tp=98.0):
    return Position(
        "BTCUSDT", "SHORT", 1.0, entry, 1700000000000,
        stop, tp, 0.0, "BBKCSqueeze", 0.0,
    )


def test_pos_meta_lazy_init_when_position_appears():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache(s)
    assert "BTCUSDT" not in s._pos_meta

    broker.positions["BTCUSDT"] = _make_long_pos()
    bar = Bar("BTCUSDT", 1700000000000, "1h", 100, 101, 99, 100, 1000)
    s.on_bar_fast(bar, 50, cache, broker)

    assert "BTCUSDT" in s._pos_meta
    meta = s._pos_meta["BTCUSDT"]
    assert meta["be_triggered"] is False
    assert meta["trail_active"] is False
    assert meta["bars_held"] == 1
    assert "R" not in meta


def test_pos_meta_cleanup_when_position_disappears():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    bar = Bar("BTCUSDT", 1700000000000, "1h", 100, 101, 99, 100, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert "BTCUSDT" in s._pos_meta

    del broker.positions["BTCUSDT"]
    s.on_bar_fast(bar, 51, cache, broker)
    assert "BTCUSDT" not in s._pos_meta


# tp_distance = 100 × 0.06 / 3 = 2.0
# defaults 0.5/0.8/0.3 → BE @ +1.0, trail @ +1.6, SL = close - 0.6


def test_be_trail_long_below_be_threshold_no_change():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    bar = Bar("BTCUSDT", 1700000000000, "1h", 100.5, 100.5, 100.5, 100.5, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert broker.stop_updates == []
    assert s._pos_meta["BTCUSDT"]["be_triggered"] is False


def test_be_trail_long_at_be_threshold_triggers_BE():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    bar = Bar("BTCUSDT", 1700000000000, "1h", 101.0, 101.0, 101.0, 101.0, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert ("BTCUSDT", 100.0) in broker.stop_updates
    assert s._pos_meta["BTCUSDT"]["be_triggered"] is True


def test_be_trail_long_BE_only_triggers_once():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    bar = Bar("BTCUSDT", 1700000000000, "1h", 101, 101, 101, 101, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 101.2, 101.2, 101.2, 101.2, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert len(broker.stop_updates) == 1


def test_be_trail_long_at_start_threshold_activates_trailing():
    s = BBKCSqueeze(exit_mode="be_trail", trail_distance_tp_frac=0.3)
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos(tp=130.0)
    # close=101.7 → move=+1.7 ≥ 0.8 × tp_dist=1.6 → BE + trail
    # trail SL = close - 0.3 × 2.0 = 101.7 - 0.6 = 101.1
    bar = Bar("BTCUSDT", 1700000000000, "1h", 101.7, 101.7, 101.7, 101.7, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert ("BTCUSDT", 100.0) in broker.stop_updates
    assert ("BTCUSDT", pytest.approx(101.1)) in broker.stop_updates
    assert s._pos_meta["BTCUSDT"]["trail_active"] is True


def test_be_trail_long_trailing_ratchets_up_only():
    s = BBKCSqueeze(exit_mode="be_trail", trail_distance_tp_frac=0.3)
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos(tp=130.0)
    bar1 = Bar("BTCUSDT", 1700000000000, "1h", 102, 102, 102, 102, 1000)
    s.on_bar_fast(bar1, 50, cache, broker)
    broker.positions["BTCUSDT"].stop_loss = 101.4

    broker.stop_updates.clear()
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 101.5, 101.5, 101.5, 101.5, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert broker.stop_updates == []

    bar3 = Bar("BTCUSDT", 1700000000002, "1h", 103, 103, 103, 103, 1000)
    s.on_bar_fast(bar3, 52, cache, broker)
    assert broker.stop_updates == [("BTCUSDT", pytest.approx(102.4))]


def test_be_trail_short_symmetry():
    s = BBKCSqueeze(exit_mode="be_trail", trail_distance_tp_frac=0.3)
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_short_pos(stop=102.33, tp=70.0)
    # close=98.3 → move = entry - close = 1.7 ≥ 1.6 → BE + trail
    # SHORT trail SL = close + 0.3 × 2.0 = 98.3 + 0.6 = 98.9
    bar = Bar("BTCUSDT", 1700000000000, "1h", 98.3, 98.3, 98.3, 98.3, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert ("BTCUSDT", 100.0) in broker.stop_updates
    assert ("BTCUSDT", pytest.approx(98.9)) in broker.stop_updates


def test_immediate_cell_be_and_trail_same_bar():
    s = BBKCSqueeze(
        exit_mode="be_trail",
        trail_be_at_tp_frac=0.49,
        trail_start_at_tp_frac=0.50,
        trail_distance_tp_frac=0.3,
    )
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos(tp=130.0)
    bar = Bar("BTCUSDT", 1700000000000, "1h", 101.0, 101.0, 101.0, 101.0, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert ("BTCUSDT", 100.0) in broker.stop_updates
    assert ("BTCUSDT", pytest.approx(100.4)) in broker.stop_updates


def test_be_trail_fixed_mode_does_not_BE():
    s = BBKCSqueeze(exit_mode="fixed")
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    bar = Bar("BTCUSDT", 1700000000000, "1h", 110, 110, 110, 110, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert broker.stop_updates == []


# ── time_stop ──


def test_time_stop_zero_does_nothing():
    s = BBKCSqueeze(exit_mode="fixed", time_stop_bars=0)
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    for k in range(100):
        bar = Bar("BTCUSDT", 1700000000000 + k, "1h", 100, 100, 100, 100, 1000)
        s.on_bar_fast(bar, 50 + k, cache, broker)
    assert broker.closes == []


def test_time_stop_triggers_at_N_bars_held():
    s = BBKCSqueeze(exit_mode="fixed", time_stop_bars=3)
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    for k in range(3):
        bar = Bar("BTCUSDT", 1700000000000 + k, "1h", 100, 100, 100, 100, 1000)
        s.on_bar_fast(bar, 50 + k, cache, broker)
    assert broker.closes == [("BTCUSDT", "time_stop")]


def test_time_stop_works_with_be_trail():
    s = BBKCSqueeze(exit_mode="be_trail", time_stop_bars=2)
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    bar1 = Bar("BTCUSDT", 1700000000000, "1h", 100.2, 100.2, 100.2, 100.2, 1000)
    s.on_bar_fast(bar1, 50, cache, broker)
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 100.4, 100.4, 100.4, 100.4, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert broker.closes == [("BTCUSDT", "time_stop")]


def test_be_trail_full_lifecycle_smoke():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos(tp=130.0)
    bar_be = Bar("BTCUSDT", 1700000000001, "1h", 101.0, 101.0, 101.0, 101.0, 1000)
    s.on_bar_fast(bar_be, 50, cache, broker)
    assert ("BTCUSDT", 100.0) in broker.stop_updates
    assert s._pos_meta["BTCUSDT"]["be_triggered"] is True
    bar_trail = Bar("BTCUSDT", 1700000000002, "1h", 102.0, 102.0, 102.0, 102.0, 1000)
    s.on_bar_fast(bar_trail, 51, cache, broker)
    assert s._pos_meta["BTCUSDT"]["trail_active"] is True


# ── drop_tp behavior at entry ────────────────────────────────────────────


from src.strategies.base import IndicatorCache


def _forced_entry_cache(n: int = 60) -> IndicatorCache:
    """Cache that forces squeeze release LONG signal at index n-1."""
    return IndicatorCache(arrays={
        "bb_upper": np.array([102.0] * n),
        "bb_mid":   np.array([100.0] * n),   # LONG: close > bb_mid
        "bb_lower": np.array([98.0] * n),
        "kc_upper": np.array([103.0] * n),
        "kc_lower": np.array([97.0] * n),
        "rsi":      np.array([55.0] * n),    # < rsi_filter (70)
        "squeeze_on": np.array([1.0] * (n - 1) + [0.0]),  # release on last bar
    })


def test_drop_tp_false_passes_take_profit_at_entry():
    s = BBKCSqueeze(exit_mode="be_trail", drop_tp=False)
    broker = _MockBroker()
    cache = _forced_entry_cache(60)
    # close > bb_mid(100) → LONG entry
    bar = Bar("BTCUSDT", 1700000000000, "1h", 101.0, 101.0, 101.0, 101.0, 1000)
    s.on_bar_fast(bar, 59, cache, broker)
    assert len(broker.buys) == 1
    sym, qty, sl, tp, _ = broker.buys[0]
    assert tp is not None
    assert tp > 101.0   # LONG: TP above entry


def test_drop_tp_true_passes_none_take_profit_at_entry():
    s = BBKCSqueeze(exit_mode="be_trail", drop_tp=True)
    broker = _MockBroker()
    cache = _forced_entry_cache(60)
    bar = Bar("BTCUSDT", 1700000000000, "1h", 101.0, 101.0, 101.0, 101.0, 1000)
    s.on_bar_fast(bar, 59, cache, broker)
    assert len(broker.buys) == 1
    sym, qty, sl, tp, _ = broker.buys[0]
    assert tp is None
    assert sl is not None and sl < 101.0   # SL still set


def test_drop_tp_true_short_also_passes_none_tp():
    s = BBKCSqueeze(exit_mode="be_trail", drop_tp=True)
    broker = _MockBroker()
    # SHORT: close < bb_mid + RSI > 30
    cache = IndicatorCache(arrays={
        "bb_upper": np.array([102.0] * 60),
        "bb_mid":   np.array([100.0] * 60),
        "bb_lower": np.array([98.0] * 60),
        "kc_upper": np.array([103.0] * 60),
        "kc_lower": np.array([97.0] * 60),
        "rsi":      np.array([45.0] * 60),
        "squeeze_on": np.array([1.0] * 59 + [0.0]),
    })
    bar = Bar("BTCUSDT", 1700000000000, "1h", 99.0, 99.0, 99.0, 99.0, 1000)
    s.on_bar_fast(bar, 59, cache, broker)
    assert len(broker.sells) == 1
    sym, qty, sl, tp, _ = broker.sells[0]
    assert tp is None
    assert sl is not None and sl > 99.0
