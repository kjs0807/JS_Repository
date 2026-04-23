"""Tests for RSIDivergence pattern."""
import math

import numpy as np
import pandas as pd

from src.core.types import BarSeries
from src.ml.patterns.rsi_divergence import RSIDivergence
from src.ml.types import MTFData


H = 3_600_000
D = 24 * H


def _series(closes, tf, step_ms, symbol="BTCUSDT"):
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            "timestamp": i * step_ms,
            "open": float(c), "high": float(c) + 0.5, "low": float(c) - 0.5,
            "close": float(c), "volume": 1.0, "turnover": 1.0,
        })
    return BarSeries(symbol=symbol, timeframe=tf, bars=pd.DataFrame(rows))


def _mtf(closes_primary):
    h1 = _series(closes_primary, "1h", H)
    n4 = max(1, len(closes_primary) // 4)
    closes_4h = [
        float(np.mean(closes_primary[i * 4 : (i + 1) * 4]))
        for i in range(n4)
    ]
    n_d = max(1, len(closes_primary) // 24)
    closes_1d = [
        float(np.mean(closes_primary[i * 24 : (i + 1) * 24]))
        for i in range(n_d)
    ]
    h4 = _series(closes_4h, "4h", 4 * H)
    d1 = _series(closes_1d, "1d", D)
    return MTFData(symbol="BTCUSDT", primary_tf="1h", series={"1h": h1, "4h": h4, "1d": d1})


def _test_pattern() -> RSIDivergence:
    """Construct an RSIDivergence with a small percentile_lookback so the
    warmup_bars stays compatible with the small synthetic fixtures used in
    these unit tests. The production defaults assume thousands of bars.
    """
    return RSIDivergence(percentile_lookback=30)


def test_detect_at_returns_none_below_warmup():
    pattern = _test_pattern()
    closes = [100.0] * 10
    mtf = _mtf(closes)
    assert pattern.detect_at(mtf, i=5) is None  # below warmup


def _bull_div_closes() -> list:
    """Synthetic closes that reliably produce a bull RSI divergence.

    Structure:
    - Phase 1 (80 bars): gentle sine oscillation around 100 — RSI warms up.
    - Phase 2 (15 bars): sharp drop to 70 — first price low, RSI oversold.
    - Phase 3 (10 bars): strong bounce to 90 — RSI recovers.
    - Phase 4 (10 bars): drop to 65 — lower price low, but RSI makes a
      higher low than Phase 2 → classic bull divergence.
    """
    phase1 = [100 + 2 * np.sin(i * 0.3) for i in range(80)]
    phase2 = list(np.linspace(100, 70, 15))
    phase3 = list(np.linspace(70, 90, 10))
    phase4 = list(np.linspace(90, 65, 10))
    return [float(x) for x in phase1 + phase2 + phase3 + phase4]


def test_detect_at_finds_bull_divergence():
    pattern = _test_pattern()
    closes = _bull_div_closes()
    mtf = _mtf(closes)
    found = False
    for i in range(pattern.warmup_bars, len(closes)):
        ev = pattern.detect_at(mtf, i)
        if ev is not None and ev.direction == "long":
            found = True
            assert ev.symbol == "BTCUSDT"
            assert "divergence_strength" in ev.metadata
            break
    assert found, "expected at least one bull divergence in synthetic series"


def test_extract_features_shape():
    pattern = _test_pattern()
    closes = _bull_div_closes()
    mtf = _mtf(closes)
    for i in range(pattern.warmup_bars, len(closes)):
        ev = pattern.detect_at(mtf, i)
        if ev is not None:
            feats = pattern.extract_features(ev, mtf)
            assert isinstance(feats, dict)
            assert "rsi_primary" in feats
            assert "divergence_strength" in feats
            for v in feats.values():
                assert isinstance(v, float)
            return
    assert False, "no event detected to extract features from"


def test_extract_features_has_all_required_keys():
    pattern = _test_pattern()
    closes = _bull_div_closes()
    mtf = _mtf(closes)
    for i in range(pattern.warmup_bars, len(closes)):
        ev = pattern.detect_at(mtf, i)
        if ev is None:
            continue
        feats = pattern.extract_features(ev, mtf)
        expected = {
            "rsi_primary",
            "divergence_strength",          # |price_slope - rsi_slope|
            "slope_divergence_ratio",       # eps-protected price_slope / rsi_slope
            "price_slope",
            "price_slope_atr_norm",         # price_slope / ATR(1h) at event
            "rsi_slope",
            "price_diff_abs", "rsi_diff_abs",
            "pivot_distance_bars",
            "pivot_prominence", "intervening_retracement_ratio",
            "dt_regular_bull", "dt_regular_bear", "dt_hidden_bull", "dt_hidden_bear",
            "adx_primary", "plus_minus_di_diff_primary",
            "bb_width_primary", "bb_width_pct_primary",
            "atr_primary_normalized", "atr_primary_pct",
            "candle_body_ratio_primary",
            "h4_ema_slope_atr_norm", "h4_rsi_14", "h4_trend_alignment",
            "d1_ema_slope_atr_norm", "d1_rsi_14", "d1_trend_alignment",
            "dist_roll_high_atr", "dist_roll_low_atr",
            "dist_swing_high_atr", "dist_swing_low_atr",
            "is_long", "confirmation_lag",
        }
        missing = expected - set(feats.keys())
        assert not missing, f"missing keys: {missing}"
        for k, v in feats.items():
            assert not math.isnan(v), f"feature {k} is NaN"
            assert math.isfinite(v), f"feature {k} is not finite: {v}"
        return
    # If we get here, no event was produced — which would be a test-data bug
    raise AssertionError("no divergence detected on synthetic bull fixture")


def test_metadata_schema_locked():
    pattern = _test_pattern()
    closes = _bull_div_closes()
    mtf = _mtf(closes)
    for i in range(pattern.warmup_bars, len(closes)):
        ev = pattern.detect_at(mtf, i)
        if ev is None:
            continue
        required = {
            "div_type", "pivot_bar_index", "confirm_bar_index",
            "first_pivot_idx", "second_pivot_idx", "divergence_strength",
        }
        missing = required - set(ev.metadata.keys())
        assert not missing, f"metadata missing keys: {missing}"
        # confirm_bar_index == event.bar_index; pivot_bar_index < confirm_bar_index
        assert ev.metadata["confirm_bar_index"] == ev.bar_index
        assert ev.metadata["pivot_bar_index"] < ev.metadata["confirm_bar_index"]
        return
    raise AssertionError("no divergence detected")
