"""Tests for location helpers (lookahead-safe rolling/swing distances)."""
import math

import numpy as np
import pandas as pd

from src.core.types import BarSeries
from src.ml.helpers.location import (
    rolling_nbar_extremes,
    confirmed_swing_highs_lows,
)


def _series(highs, lows, closes=None):
    n = len(highs)
    if closes is None:
        closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    df = pd.DataFrame({
        "timestamp": [i * 60_000 for i in range(n)],
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1.0] * n,
        "turnover": [1.0] * n,
    })
    return BarSeries(symbol="X", timeframe="1m", bars=df)


def test_rolling_extremes_first_bar_is_nan():
    s = _series([10.0, 11.0, 12.0], [9.0, 10.0, 11.0])
    rh, rl = rolling_nbar_extremes(s, n=5)
    assert math.isnan(rh[0])
    assert math.isnan(rl[0])


def test_rolling_extremes_window_smaller_than_n_at_start():
    s = _series(
        highs=[10, 12, 11, 13, 14],
        lows=[8, 9, 7, 10, 11],
    )
    rh, rl = rolling_nbar_extremes(s, n=3)
    # At i=1: window = [0], high=10, low=8
    assert rh[1] == 10.0
    assert rl[1] == 8.0
    # At i=2: window = [0,1], high=max(10,12)=12, low=min(8,9)=8
    assert rh[2] == 12.0
    assert rl[2] == 8.0
    # At i=3: window = [0,1,2], high=max(10,12,11)=12, low=min(8,9,7)=7
    assert rh[3] == 12.0
    assert rl[3] == 7.0
    # At i=4: window = [1,2,3] (n=3 back), high=max(12,11,13)=13, low=min(9,7,10)=7
    assert rh[4] == 13.0
    assert rl[4] == 7.0


def test_rolling_extremes_excludes_current_bar():
    """The current bar must NOT be in its own rolling window — otherwise
    you'd be peeking at the bar you're about to trade."""
    s = _series(
        highs=[5, 5, 5, 100, 5],  # bar 3 spike
        lows=[4, 4, 4, 3, 4],
    )
    rh, rl = rolling_nbar_extremes(s, n=5)
    # At i=3, window should be [0,1,2] — NOT include the spike at i=3.
    assert rh[3] == 5.0
    # At i=4, window should include the spike at i=3.
    assert rh[4] == 100.0


def test_confirmed_swing_highs_lows_detects_clear_pivot():
    # Clear high pivot at index 4 (value 20), confirmed by idx 7.
    highs = np.array([10, 11, 12, 15, 20, 15, 12, 11, 10, 9], dtype=float)
    lows = np.array([8, 9, 10, 13, 18, 13, 10, 9, 8, 7], dtype=float)
    s = _series(highs.tolist(), lows.tolist())
    swing_h, swing_l = confirmed_swing_highs_lows(s, confirmation_bars=3)
    # Until i < 7 (pivot confirmation), swing high is NaN
    assert math.isnan(swing_h[6])
    # At i=7 the pivot at p=4 (value 20) has 3 bars on each side → confirmed
    assert swing_h[7] == 20.0
    # And that value persists at later bars
    assert swing_h[9] == 20.0


def test_confirmed_swing_highs_lows_detects_clear_low():
    highs = np.array([20, 18, 16, 14, 12, 14, 16, 18, 20, 22], dtype=float)
    lows = np.array([18, 16, 14, 12, 10, 12, 14, 16, 18, 20], dtype=float)
    s = _series(highs.tolist(), lows.tolist())
    swing_h, swing_l = confirmed_swing_highs_lows(s, confirmation_bars=3)
    # Pivot low at p=4 (value 10), confirmed at i=7
    assert math.isnan(swing_l[6])
    assert swing_l[7] == 10.0
    assert swing_l[9] == 10.0


def test_confirmed_swing_no_false_pivot_on_flat_sequence():
    """A strictly-flat sequence must produce NO confirmed pivots (strict rule
    requires at least one strict inequality)."""
    highs = [10.0] * 20
    lows = [9.0] * 20
    s = _series(highs, lows)
    swing_h, swing_l = confirmed_swing_highs_lows(s, confirmation_bars=3)
    assert all(math.isnan(v) for v in swing_h)
    assert all(math.isnan(v) for v in swing_l)


def test_confirmed_swing_lookahead_safety():
    """Computing the full array and slicing should give the same result
    at index i as computing on a series truncated to [:i+1]."""
    rng = np.random.default_rng(0)
    n = 40
    closes = 100 + np.cumsum(rng.normal(0, 0.5, n))
    highs = (closes + 0.5).tolist()
    lows = (closes - 0.5).tolist()
    s_full = _series(highs, lows, closes.tolist())
    swing_h_full, swing_l_full = confirmed_swing_highs_lows(s_full, confirmation_bars=3)
    for i in range(5, n):
        s_trunc = _series(highs[: i + 1], lows[: i + 1], closes[: i + 1].tolist())
        swing_h_t, swing_l_t = confirmed_swing_highs_lows(s_trunc, confirmation_bars=3)
        # At index i, both computations must agree (or both NaN)
        if math.isnan(swing_h_full[i]) and math.isnan(swing_h_t[i]):
            pass
        else:
            assert swing_h_full[i] == swing_h_t[i], f"mismatch at i={i}"
        if math.isnan(swing_l_full[i]) and math.isnan(swing_l_t[i]):
            pass
        else:
            assert swing_l_full[i] == swing_l_t[i], f"mismatch at i={i}"
