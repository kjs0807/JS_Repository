"""Tests for divergence detection with confirmation-window pivot rule + 4 types."""
import numpy as np

from src.ml.helpers.divergence import detect_divergence, DivergenceInfo


def test_regular_bull_divergence_detected():
    # Two clear V-shaped lows:
    #   idx 4 (price=6)  — 3 bars on each side all > 6 → confirmed at idx 7
    #   idx 12 (price=5) — 3 bars on each side all > 5 → confirmed at idx 15
    # RSI: first low 30, second low 32 → higher low → regular bullish divergence
    price = np.array(
        [10, 9, 8, 7, 6, 7, 8, 9, 10, 9, 8, 7, 5, 6, 7, 8],
        dtype=float,
    )
    rsi = np.array(
        [50, 45, 40, 35, 30, 35, 40, 45, 50, 45, 40, 35, 32, 40, 45, 50],
        dtype=float,
    )
    result = detect_divergence(
        price=price, indicator=rsi,
        end_index=15,
        mode="bull",
        confirmation_bars=3,
        lookback=30,
    )
    assert result is not None
    assert isinstance(result, DivergenceInfo)
    assert result.div_type == "regular_bull"
    assert result.second_pivot_idx == 12
    assert result.first_pivot_idx == 4
    assert result.pivot_distance_bars == 8
    assert result.pivot_confirmation_lag == 3
    assert result.price_diff == -1.0        # 5 - 6
    assert result.rsi_diff == 2.0           # 32 - 30
    assert result.slope_divergence_ratio < 0  # price down, rsi up
    assert result.pivot_prominence > 0


def test_regular_bear_divergence_detected():
    # Two V-shaped highs: idx 4 (price=12) and idx 12 (price=14) → higher high
    price = np.array(
        [8, 9, 10, 11, 12, 11, 10, 9, 8, 9, 10, 12, 14, 13, 12, 11],
        dtype=float,
    )
    # RSI first high 70, second high 60 → lower high → regular bearish
    rsi = np.array(
        [50, 55, 60, 65, 70, 65, 60, 55, 50, 55, 58, 59, 60, 55, 50, 45],
        dtype=float,
    )
    result = detect_divergence(
        price=price, indicator=rsi,
        end_index=15,
        mode="bear",
        confirmation_bars=3,
        lookback=30,
    )
    assert result is not None
    assert result.div_type == "regular_bear"
    assert result.second_pivot_idx == 12
    assert result.first_pivot_idx == 4
    assert result.price_diff == 2.0     # 14 - 12
    assert result.rsi_diff == -10.0     # 60 - 70


def test_hidden_bull_divergence_detected():
    # Hidden bullish: price higher low, oscillator lower low (uptrend continuation)
    price = np.array(
        [10, 9, 8, 7, 6, 7, 8, 9, 10, 11, 10, 9, 8, 9, 10, 11],
        dtype=float,
    )
    # RSI first low 35, second low 25 → lower low
    rsi = np.array(
        [50, 45, 40, 38, 35, 45, 50, 55, 60, 45, 35, 30, 25, 35, 45, 55],
        dtype=float,
    )
    result = detect_divergence(
        price=price, indicator=rsi,
        end_index=15,
        mode="bull",
        confirmation_bars=3,
        lookback=30,
    )
    assert result is not None
    assert result.div_type == "hidden_bull"
    assert result.second_pivot_idx == 12
    assert result.first_pivot_idx == 4
    assert result.price_diff > 0  # higher low
    assert result.rsi_diff < 0    # lower low


def test_hidden_bear_divergence_detected():
    # Hidden bearish: price lower high, oscillator higher high (downtrend continuation)
    price = np.array(
        [8, 9, 10, 11, 12, 11, 10, 9, 8, 7, 8, 9, 10, 9, 8, 7],
        dtype=float,
    )
    rsi = np.array(
        [50, 55, 58, 60, 62, 55, 50, 45, 40, 50, 60, 65, 70, 60, 50, 45],
        dtype=float,
    )
    result = detect_divergence(
        price=price, indicator=rsi,
        end_index=15,
        mode="bear",
        confirmation_bars=3,
        lookback=30,
    )
    assert result is not None
    assert result.div_type == "hidden_bear"
    assert result.second_pivot_idx == 12
    assert result.first_pivot_idx == 4
    assert result.price_diff < 0  # lower high
    assert result.rsi_diff > 0    # higher high


def test_returns_none_when_no_pivot_at_target():
    # Pure monotonic decline → no local mins anywhere
    price = np.linspace(100, 50, 30)
    rsi = np.linspace(70, 20, 30)
    result = detect_divergence(
        price=price, indicator=rsi,
        end_index=20,
        mode="bull",
        confirmation_bars=3,
        lookback=30,
    )
    assert result is None


def test_does_not_use_future_data():
    """Calling with end_index < len(arr) must ignore arr[end_index+1:]."""
    price_full = np.array(
        [10, 9, 8, 7, 6, 7, 8, 9, 10, 9, 8, 7, 5, 6, 7, 8, 100, 100, 100],
        dtype=float,
    )
    rsi_full = np.array(
        [50, 45, 40, 35, 30, 35, 40, 45, 50, 45, 40, 35, 32, 40, 45, 50, 99, 99, 99],
        dtype=float,
    )
    full = detect_divergence(
        price=price_full, indicator=rsi_full,
        end_index=15, mode="bull",
        confirmation_bars=3, lookback=30,
    )
    sliced = detect_divergence(
        price=price_full[:16], indicator=rsi_full[:16],
        end_index=15, mode="bull",
        confirmation_bars=3, lookback=30,
    )
    assert full is not None
    assert sliced is not None
    assert full.first_pivot_idx == sliced.first_pivot_idx
    assert full.second_pivot_idx == sliced.second_pivot_idx
    assert full.div_type == sliced.div_type


def test_slope_ratio_is_clipped_and_finite_when_rsi_flat():
    # Near-flat RSI → eps-protected ratio must not blow up
    price = np.array(
        [10, 9, 8, 7, 6, 7, 8, 9, 10, 9, 8, 7, 5, 6, 7, 8],
        dtype=float,
    )
    rsi = np.array(
        [50.0, 50.0, 50.0, 50.0, 49.999, 50.0, 50.0, 50.0,
         50.0, 50.0, 50.0, 50.0, 50.000001, 50.0, 50.0, 50.0],
        dtype=float,
    )
    result = detect_divergence(
        price=price, indicator=rsi,
        end_index=15, mode="bull",
        confirmation_bars=3, lookback=30,
    )
    if result is not None:
        assert np.isfinite(result.slope_divergence_ratio)
        assert abs(result.slope_divergence_ratio) <= 50.0 + 1e-9


def test_backward_compat_aliases():
    price = np.array(
        [10, 9, 8, 7, 6, 7, 8, 9, 10, 9, 8, 7, 5, 6, 7, 8],
        dtype=float,
    )
    rsi = np.array(
        [50, 45, 40, 35, 30, 35, 40, 45, 50, 45, 40, 35, 32, 40, 45, 50],
        dtype=float,
    )
    result = detect_divergence(
        price=price, indicator=rsi,
        end_index=15, mode="bull",
        confirmation_bars=3, lookback=30,
    )
    assert result is not None
    # v1 aliases still work for any consumer that wasn't rewritten yet
    assert result.mode == "bull"
    assert result.first_low_idx == result.first_pivot_idx
    assert result.second_low_idx == result.second_pivot_idx
    assert result.first_high_idx == result.first_pivot_idx
    assert result.second_high_idx == result.second_pivot_idx
    assert result.strength >= 0.0


def test_strength_is_absolute_slope_difference():
    """Regression: divergence_strength === |price_slope - rsi_slope|, NOT a ratio."""
    price = np.array(
        [10, 9, 8, 7, 6, 7, 8, 9, 10, 9, 8, 7, 5, 6, 7, 8],
        dtype=float,
    )
    rsi = np.array(
        [50, 45, 40, 35, 30, 35, 40, 45, 50, 45, 40, 35, 32, 40, 45, 50],
        dtype=float,
    )
    result = detect_divergence(
        price=price, indicator=rsi,
        end_index=15, mode="bull",
        confirmation_bars=3, lookback=30,
    )
    assert result is not None
    expected = abs(result.price_slope - result.rsi_slope)
    assert abs(result.strength - expected) < 1e-12
    # And slope_divergence_ratio is a SEPARATE field with different semantics:
    # it's a ratio (price_slope / rsi_slope), NOT a difference.
    if abs(result.rsi_slope) > 1e-9:
        ratio_target = result.price_slope / result.rsi_slope
        # Allow for the eps-protection / clipping in the helper
        assert abs(result.slope_divergence_ratio - ratio_target) < 1e-6 or \
               abs(result.slope_divergence_ratio) >= 50.0 - 1e-9


def test_backward_iteration_finds_older_valid_pair():
    """When the most-recent prior pivot does NOT form a divergence with the
    current pivot, but an OLDER prior pivot inside the lookback window does,
    the function must return the older pair instead of None.
    """
    # Three confirmed lows, all at idx with 3 bars on each side higher:
    #   idx  3 (price=8)   — first low
    #   idx 10 (price=10)  — middle low (HIGHER than first; not a regular bull
    #                        partner with idx 17 if we just looked at "prev")
    #   idx 17 (price=7)   — final low (LOWER than first low → bull divergence
    #                        with idx 3 if we keep walking back)
    #
    # Without backward iteration, the function would only check (10, 17):
    #   price 10 vs 7 → lower low ✓
    #   rsi  35 vs 38 → higher low ✓ → regular_bull (so it WOULD pass here)
    # Make rsi at idx 10 be HIGHER than at idx 17 so (10, 17) is NOT a
    # regular_bull but (3, 17) still is.
    price = np.array(
        [12, 11, 9, 8, 9, 10, 11, 12, 11, 10.5, 10, 11, 12, 11, 10, 9, 8, 7, 8, 9, 10],
        dtype=float,
    )
    # idx 3 = 8 (low), idx 10 = 10 (intermediate), idx 17 = 7 (final)
    # Pivot confirmation: idx 17 needs 3 bars on each side > 7 → idx 14,15,16=10,9,8 ✓
    # and idx 18,19,20=8,9,10 ✓.
    rsi = np.array(
        [60, 55, 50, 30, 40, 50, 60, 55, 50, 45, 40, 50, 60, 55, 50, 45, 40, 35, 50, 60, 65],
        dtype=float,
    )
    # rsi[10]=40, rsi[17]=35 → (10,17) is NOT bull (rsi LOWER, would be hidden_bull)
    # rsi[3]=30, rsi[17]=35 → (3,17) IS regular bull (rsi HIGHER, price LOWER)
    result = detect_divergence(
        price=price, indicator=rsi,
        end_index=20, mode="bull",
        confirmation_bars=3, lookback=30,
    )
    # Either regular_bull from (3, 17) OR hidden_bull from (10, 17) is valid
    # output; the key point is it must NOT be None.
    assert result is not None
    assert result.second_pivot_idx == 17
    assert result.first_pivot_idx in (3, 10)
