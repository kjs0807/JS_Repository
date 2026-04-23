"""Tests for src/ml/helpers/indicators_mtf.py — thin wrapper around existing indicators."""
import math

import numpy as np
import pandas as pd

from src.core.types import BarSeries
from src.ml.helpers.indicators_mtf import compute_rsi, compute_atr, compute_ema


def _series_from_closes(closes):
    """Build a minimal BarSeries from a list of closes (other fields trivial)."""
    df = pd.DataFrame({
        "timestamp": [i * 60_000 for i in range(len(closes))],
        "open": closes,
        "high": [c + 1.0 for c in closes],
        "low": [c - 1.0 for c in closes],
        "close": closes,
        "volume": [1.0] * len(closes),
        "turnover": [1.0] * len(closes),
    })
    return BarSeries(symbol="X", timeframe="1m", bars=df)


def test_compute_rsi_returns_numpy_array():
    s = _series_from_closes([100.0 + i * 0.1 for i in range(50)])
    rsi = compute_rsi(s, period=14)
    assert isinstance(rsi, np.ndarray)
    assert len(rsi) == 50


def test_compute_rsi_warmup_is_nan():
    s = _series_from_closes([100.0 + i * 0.1 for i in range(50)])
    rsi = compute_rsi(s, period=14)
    # First `period` values are NaN per the existing implementation
    assert all(math.isnan(v) for v in rsi[:14])
    # After warmup, valid values
    assert not math.isnan(rsi[20])


def test_compute_atr_warmup_is_nan_then_positive():
    closes = [100.0 + i for i in range(30)]
    s = _series_from_closes(closes)
    atr = compute_atr(s, period=14)
    assert isinstance(atr, np.ndarray)
    assert all(math.isnan(v) for v in atr[:14])
    assert atr[20] > 0


def test_compute_ema_warmup_is_nan_then_value():
    s = _series_from_closes([100.0 + i for i in range(20)])
    ema = compute_ema(s, period=10)
    assert isinstance(ema, np.ndarray)
    # First period-1 values are NaN per the existing implementation
    assert all(math.isnan(v) for v in ema[:9])
    assert not math.isnan(ema[15])


def test_compute_rsi_known_textbook_sequence():
    # Wilder RSI period=14 on a textbook sequence: result around 70+ at first valid index
    closes = [
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
        45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
        46.03, 46.41, 46.22, 45.64, 46.21, 46.25,
    ]
    s = _series_from_closes(closes)
    rsi = compute_rsi(s, period=14)
    # Pick an index well past warmup; expect valid RSI in (0, 100).
    last = rsi[-1]
    assert not math.isnan(last)
    assert 0.0 < last < 100.0


# ---------------------------------------------------------------------------
# Tests for compute_adx, compute_bb_width, compute_percentile_rank (Task B1)
# ---------------------------------------------------------------------------

import math  # noqa: F811 — already imported above; kept for clarity in appended block

import numpy as np  # noqa: F811
import pandas as pd  # noqa: F811

from src.core.types import BarSeries  # noqa: F811
from src.ml.helpers.indicators_mtf import (
    compute_adx,
    compute_bb_width,
    compute_percentile_rank,
)


def _series_for_adx(n=60, seed=0):
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0.2, 0.5, n))
    df = pd.DataFrame({
        "timestamp": [i * 60_000 for i in range(n)],
        "open": closes,
        "high": closes + 1.0,
        "low": closes - 1.0,
        "close": closes,
        "volume": [1.0] * n,
        "turnover": [1.0] * n,
    })
    return BarSeries(symbol="X", timeframe="1m", bars=df)


def test_compute_adx_returns_three_arrays():
    s = _series_for_adx()
    adx, plus_di, minus_di = compute_adx(s, period=14)
    assert isinstance(adx, np.ndarray)
    assert isinstance(plus_di, np.ndarray)
    assert isinstance(minus_di, np.ndarray)
    assert len(adx) == len(plus_di) == len(minus_di) == len(s)
    assert all(math.isnan(v) for v in adx[:14])


def test_compute_bb_width_shape():
    closes = [100.0 + 0.5 * i for i in range(40)]
    df = pd.DataFrame({
        "timestamp": [i * 60_000 for i in range(40)],
        "open": closes, "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes], "close": closes,
        "volume": [1.0] * 40, "turnover": [1.0] * 40,
    })
    s = BarSeries(symbol="X", timeframe="1m", bars=df)
    bbw = compute_bb_width(s, period=20, std=2.0)
    assert isinstance(bbw, np.ndarray)
    assert len(bbw) == 40
    # Something positive after warmup
    assert not math.isnan(bbw[25])
    assert bbw[25] > 0


def test_percentile_rank_on_monotonic_sequence():
    arr = np.array([1, 2, 3, 4, 5], dtype=float)
    pr = compute_percentile_rank(arr, lookback=5)
    # Each successive value is the largest so far → rank 1.0
    assert pr[0] == 1.0
    assert pr[-1] == 1.0


def test_percentile_rank_windowed():
    arr = np.array([5, 3, 1, 4, 2], dtype=float)
    pr = compute_percentile_rank(arr, lookback=3)
    # At i=0 window=[5] → 1.0
    # At i=1 window=[5,3], 3 is the smallest → 0.5 (rank 1/2)
    # At i=2 window=[5,3,1], 1 is the smallest → 1/3
    # At i=3 window=[3,1,4], 4 is largest → 1.0
    # At i=4 window=[1,4,2], 2 is middle → 2/3
    assert pr[0] == 1.0
    assert abs(pr[1] - 0.5) < 1e-9
    assert abs(pr[2] - 1.0 / 3.0) < 1e-9
    assert pr[3] == 1.0
    assert abs(pr[4] - 2.0 / 3.0) < 1e-9


def test_percentile_rank_nan_propagation():
    arr = np.array([1.0, np.nan, 3.0, 4.0], dtype=float)
    pr = compute_percentile_rank(arr, lookback=4)
    # NaN input → NaN output at that index
    assert math.isnan(pr[1])
    # Other positions should still get a rank from the non-NaN window members
    assert not math.isnan(pr[0])
    assert not math.isnan(pr[2])
    assert not math.isnan(pr[3])
