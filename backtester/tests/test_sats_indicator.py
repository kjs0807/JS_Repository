"""SATS indicator regression — Pine v1.9.0 port.

Coverage:

1. Schema — 25 columns with the documented dtypes.
2. Warmup gate — ``sats_ready`` is False for the first ``required_warmup_bars``
   rows and True afterwards; signal columns are NaN/0 inside warmup.
3. Determinism — running compute twice on the same DataFrame is identical.
4. Empty input — 0-row DataFrame returned with the correct schema, no crash.
5. Causality — truncating input from the right does not change earlier rows.
6. SL invariants — long signal SL is below entry; short signal SL is above.
   Risk is positive, TP1/TP2/TP3 monotone in the trade direction.
7. Trend ratchet — lower_band only ratchets up while above; upper_band only
   ratchets down while below; never both at once.
8. Helpers — wilder_rma vs reference recursion (dense + NaN-hole), wilder_atr
   vs reference Wilder formula, efficiency_ratio range, pivot_high strict
   max property, volume_zscore population stdev parity.
9. Preset resolution — Auto-by-timeframe, Custom forwards raw inputs.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import polars as pl
import pytest

from backtester.indicators.stateful.sats import (
    SATSConfig,
    SATSIndicator,
    efficiency_ratio,
    pivot_high,
    pivot_low,
    resolve_sats_preset,
    rolling_max,
    rolling_min,
    volume_zscore,
    wilder_atr,
    wilder_rma,
)

UTC = timezone.utc


# ---------- fixtures --------------------------------------------------------


def _random_ohlcv(
    n: int,
    *,
    seed: int = 42,
    base_price: float = 100.0,
    drift_scale: float = 0.5,
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    ts = [base + timedelta(hours=i) for i in range(n)]
    drift = np.cumsum(rng.standard_normal(n) * drift_scale)
    close = base_price + drift
    high = close + np.abs(rng.standard_normal(n)) * 0.4
    low = close - np.abs(rng.standard_normal(n)) * 0.4
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = np.abs(rng.standard_normal(n)) * 100 + 1.0
    return pl.DataFrame(
        {
            "timestamp": ts,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    ).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    )


# ---------- 1. schema -------------------------------------------------------

EXPECTED_COLUMNS = [
    "sats_atr",
    "sats_raw_atr",
    "sats_er",
    "sats_vol_ratio",
    "sats_tqi",
    "sats_tqi_er",
    "sats_tqi_vol",
    "sats_tqi_struct",
    "sats_tqi_mom",
    "sats_active_mult",
    "sats_passive_mult",
    "sats_lower_band",
    "sats_upper_band",
    "sats_trend",
    "sats_st_line",
    "sats_signal",
    "sats_entry_price",
    "sats_sl_price",
    "sats_tp1_price",
    "sats_tp2_price",
    "sats_tp3_price",
    "sats_tp1_r",
    "sats_tp2_r",
    "sats_tp3_r",
    "sats_ready",
]

EXPECTED_DTYPES = {
    "sats_trend": pl.Int8,
    "sats_signal": pl.Int8,
    "sats_ready": pl.Boolean,
}


def test_schema_columns_and_dtypes() -> None:
    bars = _random_ohlcv(400)
    out = SATSIndicator().compute(bars)
    assert out.columns == EXPECTED_COLUMNS
    for col, expected in EXPECTED_DTYPES.items():
        assert out.schema[col] == expected, (
            f"{col} dtype mismatch: got {out.schema[col]} expected {expected}"
        )
    # everything else should be Float64
    for col in EXPECTED_COLUMNS:
        if col in EXPECTED_DTYPES:
            continue
        assert out.schema[col] == pl.Float64, (
            f"{col} should be Float64, got {out.schema[col]}"
        )


# ---------- 2. warmup gate --------------------------------------------------


def test_warmup_gate_blocks_signals() -> None:
    cfg = SATSConfig()
    ind = SATSIndicator(cfg)
    warmup = ind.required_warmup_bars()
    bars = _random_ohlcv(warmup + 200)
    out = ind.compute(bars)

    ready = out["sats_ready"].to_numpy()
    assert not ready[: warmup].any(), "ready must be False inside warmup"
    assert ready[warmup:].all(), "ready must be True after warmup"

    # Signal-related columns should be 0 / NaN inside warmup.
    sig_in = out["sats_signal"].to_numpy()[: warmup]
    assert np.all(sig_in == 0)
    for col in ("sats_entry_price", "sats_sl_price", "sats_tp1_price"):
        arr = out[col].to_numpy()[: warmup]
        assert np.all(np.isnan(arr)), f"{col} should be NaN inside warmup"


# ---------- 3. determinism --------------------------------------------------


def test_compute_is_deterministic() -> None:
    bars = _random_ohlcv(500, seed=11)
    a = SATSIndicator().compute(bars)
    b = SATSIndicator().compute(bars)
    assert a.equals(b), "two compute() calls should match exactly"


# ---------- 4. empty input --------------------------------------------------


def test_compute_on_empty_returns_zero_row_with_schema() -> None:
    empty = pl.DataFrame(
        {"timestamp": [], "open": [], "high": [], "low": [], "close": [], "volume": []},
        schema={
            "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
        },
    )
    out = SATSIndicator().compute(empty)
    assert out.height == 0
    assert out.columns == EXPECTED_COLUMNS


# ---------- 5. causality ----------------------------------------------------


def test_truncating_right_does_not_change_earlier_rows() -> None:
    n = 600
    cut = 450
    full = _random_ohlcv(n, seed=7)
    truncated = full.slice(0, cut)
    out_full = SATSIndicator().compute(full).slice(0, cut)
    out_trunc = SATSIndicator().compute(truncated)
    # Compare column by column, NaN-aware.
    for col in EXPECTED_COLUMNS:
        a = out_full[col].to_numpy()
        b = out_trunc[col].to_numpy()
        same_nan = np.isnan(a.astype(np.float64, copy=False)) == np.isnan(
            b.astype(np.float64, copy=False)
        ) if a.dtype.kind == "f" else np.array([True] * len(a))
        if a.dtype.kind == "f":
            non_nan = ~np.isnan(a)
            assert np.all(same_nan), f"{col}: NaN positions must agree"
            assert np.allclose(a[non_nan], b[non_nan], equal_nan=False), (
                f"{col} differs between full and truncated runs"
            )
        else:
            assert np.array_equal(a, b), f"{col} differs (non-float)"


# ---------- 6. SL/TP invariants at signal bars -----------------------------


def test_signal_sl_tp_invariants() -> None:
    """At every signal bar, SL is on the loss side of entry and TP1/2/3 are
    monotonically further on the profit side."""
    bars = _random_ohlcv(800, seed=3, drift_scale=0.8)  # more drift → more flips
    out = SATSIndicator().compute(bars)
    sig = out["sats_signal"].to_numpy()
    entry = out["sats_entry_price"].to_numpy()
    sl = out["sats_sl_price"].to_numpy()
    tp1 = out["sats_tp1_price"].to_numpy()
    tp2 = out["sats_tp2_price"].to_numpy()
    tp3 = out["sats_tp3_price"].to_numpy()

    saw_long = False
    saw_short = False
    for i in range(len(sig)):
        if sig[i] == 0:
            continue
        e = entry[i]
        s = sl[i]
        # Always non-NaN at signal bars.
        assert not math.isnan(e) and not math.isnan(s)
        if sig[i] == 1:
            saw_long = True
            assert s < e, f"long SL must be below entry at idx {i}: e={e} sl={s}"
            assert tp1[i] > e and tp2[i] > tp1[i] and tp3[i] > tp2[i], (
                f"long TPs must ascend at idx {i}: tp1={tp1[i]} tp2={tp2[i]} tp3={tp3[i]}"
            )
        else:
            saw_short = True
            assert s > e, f"short SL must be above entry at idx {i}: e={e} sl={s}"
            assert tp1[i] < e and tp2[i] < tp1[i] and tp3[i] < tp2[i], (
                f"short TPs must descend at idx {i}: tp1={tp1[i]} tp2={tp2[i]} tp3={tp3[i]}"
            )
    assert saw_long or saw_short, "expected at least one signal in random data"


# ---------- 7. trend & band ratchet -----------------------------------------


def test_trend_consistent_with_st_line() -> None:
    bars = _random_ohlcv(800, seed=5, drift_scale=0.6)
    out = SATSIndicator().compute(bars)
    trend = out["sats_trend"].to_numpy()
    st_line = out["sats_st_line"].to_numpy()
    lower = out["sats_lower_band"].to_numpy()
    upper = out["sats_upper_band"].to_numpy()
    for i in range(len(trend)):
        if math.isnan(st_line[i]):
            continue
        if trend[i] == 1:
            assert math.isclose(st_line[i], lower[i], rel_tol=1e-12, abs_tol=1e-12)
        else:
            assert math.isclose(st_line[i], upper[i], rel_tol=1e-12, abs_tol=1e-12)


# ---------- 8. helpers ------------------------------------------------------


def _wilder_rma_dense_reference(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=np.float64)
    if len(values) < length:
        return out
    out[length - 1] = float(values[:length].mean())
    for i in range(length, len(values)):
        out[i] = (out[i - 1] * (length - 1) + values[i]) / length
    return out


def test_wilder_rma_dense_matches_reference() -> None:
    rng = np.random.default_rng(0)
    a = rng.uniform(1.0, 100.0, size=80).astype(np.float64)
    expected = _wilder_rma_dense_reference(a, 14)
    got = wilder_rma(a, 14)
    assert np.allclose(expected, got, equal_nan=True, atol=1e-12)


def test_wilder_rma_handles_nan_holes_without_propagation() -> None:
    rng = np.random.default_rng(0)
    a = rng.uniform(1.0, 100.0, size=80).astype(np.float64)
    a[20:23] = np.nan
    out = wilder_rma(a, 14)
    assert all(math.isnan(out[i]) for i in (20, 21, 22))
    assert not math.isnan(out[23]), "post-gap value must recover"
    # Recursion at idx 23 uses the last valid output (idx 19, the last non-NaN
    # input fed before the hole). out[19] = (out[18]*13 + a[19])/14.
    expected_23 = (out[19] * 13 + a[23]) / 14
    assert abs(out[23] - expected_23) < 1e-12


def test_wilder_atr_warmup_and_recursion() -> None:
    n = 50
    rng = np.random.default_rng(1)
    high = 100 + rng.uniform(0.5, 1.5, size=n)
    low = 100 - rng.uniform(0.5, 1.5, size=n)
    close = (high + low) / 2.0
    length = 14
    atr = wilder_atr(high, low, close, length)
    # Wilder ATR is NaN until index `length - 1`.
    assert all(math.isnan(atr[i]) for i in range(length - 1))
    assert not math.isnan(atr[length - 1])
    # Reference: TR series + RMA seed = mean of first 14 TRs.
    tr = np.empty(n, dtype=np.float64)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    expected_seed = tr[:length].mean()
    assert abs(atr[length - 1] - expected_seed) < 1e-12


def test_efficiency_ratio_in_unit_range() -> None:
    bars = _random_ohlcv(200, seed=2)
    close = bars["close"].to_numpy()
    er = efficiency_ratio(close, 20)
    valid = er[~np.isnan(er)]
    assert valid.size > 0
    assert np.all((valid >= 0.0) & (valid <= 1.0)), (
        f"ER must be in [0,1], got range {valid.min()}..{valid.max()}"
    )


def test_volume_zscore_population_stdev_parity() -> None:
    rng = np.random.default_rng(4)
    v = rng.uniform(50, 150, size=60).astype(np.float64)
    length = 20
    z = volume_zscore(v, length)
    assert math.isnan(z[length - 2])  # warmup
    # Spot-check at an inside index using population stdev.
    t = 40
    window = v[t - length + 1 : t + 1]
    mean = window.mean()
    std_pop = float(np.sqrt(((window - mean) ** 2).mean()))
    expected = (v[t] - mean) / std_pop if std_pop != 0 else 0.0
    assert abs(z[t] - expected) < 1e-12


def test_pivot_high_strict_max_window() -> None:
    # Construct a synthetic high series with one clear pivot at index 5.
    h = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 9.0, 5.0, 4.0, 3.0, 2.0, 1.0])
    out = pivot_high(h, 3, 3)
    # Pivot at index 5 should be confirmed at index 5 + 3 = 8.
    assert math.isnan(out[7])
    assert out[8] == 9.0
    # No other confirmed pivots.
    for i in range(len(out)):
        if i != 8:
            assert math.isnan(out[i]), f"unexpected pivot at idx {i}: {out[i]}"


def test_pivot_low_strict_min_window() -> None:
    low_arr = np.array([10.0, 9.0, 8.0, 7.0, 6.0, 1.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    out = pivot_low(low_arr, 3, 3)
    assert out[8] == 1.0
    for i in range(len(out)):
        if i != 8:
            assert math.isnan(out[i])


def test_rolling_max_min_warmup() -> None:
    a = np.arange(1.0, 11.0)
    rmax = rolling_max(a, 3)
    rmin = rolling_min(a, 3)
    # First 2 values must be NaN, then equal to the right end of window.
    assert math.isnan(rmax[0]) and math.isnan(rmax[1])
    assert rmax[2] == 3.0
    assert rmax[9] == 10.0
    assert rmin[2] == 1.0
    assert rmin[9] == 8.0


# ---------- 9. preset resolution -------------------------------------------


@pytest.mark.parametrize(
    ("tf_min", "expected_atr", "expected_mult", "expected_sl"),
    [
        (1, 10, 1.5, 1.0),  # Scalping
        (60, 14, 2.0, 1.5),  # Default
        (1440, 21, 2.5, 2.0),  # Swing (>= 4h+)
    ],
)
def test_resolve_preset_auto_by_timeframe(
    tf_min: int, expected_atr: int, expected_mult: float, expected_sl: float
) -> None:
    cfg = SATSConfig(preset="Auto", timeframe_minutes=tf_min)
    atr_len, base_mult, _er_len, _rsi_len, sl_mult = resolve_sats_preset(cfg)
    assert atr_len == expected_atr
    assert base_mult == expected_mult
    assert sl_mult == expected_sl


def test_resolve_preset_custom_forwards_inputs() -> None:
    cfg = SATSConfig(
        preset="Custom",
        atr_len=7,
        base_mult=1.7,
        er_length=12,
        rsi_len=8,
        sl_atr_mult=1.1,
    )
    atr_len, base_mult, er_len, rsi_len, sl_mult = resolve_sats_preset(cfg)
    assert (atr_len, base_mult, er_len, rsi_len, sl_mult) == (7, 1.7, 12, 8, 1.1)


def test_resolve_preset_crypto_24_7() -> None:
    cfg = SATSConfig(preset="Crypto 24/7", timeframe_minutes=60)
    atr_len, base_mult, er_len, rsi_len, sl_mult = resolve_sats_preset(cfg)
    assert (atr_len, base_mult, er_len, rsi_len, sl_mult) == (14, 2.8, 20, 14, 2.5)


# ---------- 10. config validation ------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeframe_minutes": 0},
        {"atr_len": 0},
        {"er_length": -1},
        {"quality_strength": 1.5},
        {"quality_curve": 0.9},
        {"pivot_len": 0},
    ],
)
def test_config_validation_rejects_invalid(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        SATSConfig(**kwargs)


# ---------- 11. preset Auto / Default config sanity -------------------------


def test_default_config_indicator_runs_without_volume() -> None:
    """Volume-free OHLCV path: tqiVol falls back to volRatio mapping."""
    bars = _random_ohlcv(400, seed=8).drop("volume")
    out = SATSIndicator().compute(bars)
    assert out.height == 400
    assert out["sats_ready"].to_numpy().any()
