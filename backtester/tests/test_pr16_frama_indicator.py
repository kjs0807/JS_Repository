"""PR 16 — FRAMAChannel indicator regression.

Coverage:
1. Output schema — all 7 expected columns (5 numeric + 2 bool).
2. Causality — full vs truncated input agree on every overlapping row.
3. Validation — odd / <2 length, distance <=0, smoothing<1, vol_window<1 all
   raise ValueError.
4. Determinism — running compute twice on the same DataFrame produces identical
   values.
5. Crossover semantics — synthetic fixture forces a known break_up bar.
6. Empty input — compute on empty bars returns 0-row DataFrame with right
   schema (no crash).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl
import pytest

from backtester.indicators.stateful.frama import FRAMAChannel

UTC = timezone.utc


# ---------- helpers ---------------------------------------------------------


def _random_ohlcv(n: int, *, seed: int = 42, base_price: float = 100.0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    ts = [base + timedelta(hours=i) for i in range(n)]
    drift = np.cumsum(rng.standard_normal(n) * 0.5)
    close = base_price + drift
    high = close + np.abs(rng.standard_normal(n)) * 0.3
    low = close - np.abs(rng.standard_normal(n)) * 0.3
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


def _safe_eq(a: object, b: object) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        return abs(a - b) < 1e-12
    return a == b


# ---------- 1. schema -------------------------------------------------------


def test_indicator_outputs_expected_columns() -> None:
    bars = _random_ohlcv(400)
    out = FRAMAChannel(length=26, distance=1.5).compute(bars)
    assert out.columns == [
        "frama",
        "frama_upper",
        "frama_lower",
        "frama_alpha",
        "frama_dimension",
        "frama_break_up",
        "frama_break_dn",
    ]
    assert out.height == bars.height
    # bool columns
    assert out.schema["frama_break_up"] == pl.Boolean
    assert out.schema["frama_break_dn"] == pl.Boolean


def test_required_warmup_bars_matches_max_of_length_smoothing_and_vol_window() -> None:
    assert FRAMAChannel(length=26, smoothing=5, volatility_window=200).required_warmup_bars() == 200
    # When length+smoothing > volatility_window the larger value wins.
    assert FRAMAChannel(length=300, smoothing=5, volatility_window=50).required_warmup_bars() == 305


# ---------- 2. causality / no-lookahead -------------------------------------


def test_no_lookahead_truncated_input_matches_full_input_on_overlap() -> None:
    bars = _random_ohlcv(500, seed=7)
    fc = FRAMAChannel(length=26, distance=1.5)
    full = fc.compute(bars)
    trunc = fc.compute(bars.head(300))
    cols = [
        "frama",
        "frama_upper",
        "frama_lower",
        "frama_alpha",
        "frama_dimension",
        "frama_break_up",
        "frama_break_dn",
    ]
    for col in cols:
        for i in range(300):
            a = full[col][i]
            b = trunc[col][i]
            assert _safe_eq(a, b), f"{col}[{i}] full={a!r} trunc={b!r}"


# ---------- 3. validation ---------------------------------------------------


@pytest.mark.parametrize("length", [1, 0, -2])
def test_length_below_two_raises(length: int) -> None:
    with pytest.raises(ValueError, match="length"):
        FRAMAChannel(length=length)


def test_odd_length_raises() -> None:
    with pytest.raises(ValueError, match="even"):
        FRAMAChannel(length=27)


@pytest.mark.parametrize("distance", [0, -0.1])
def test_distance_non_positive_raises(distance: float) -> None:
    with pytest.raises(ValueError, match="distance"):
        FRAMAChannel(distance=distance)


@pytest.mark.parametrize("smoothing", [0, -1])
def test_smoothing_below_one_raises(smoothing: int) -> None:
    with pytest.raises(ValueError, match="smoothing"):
        FRAMAChannel(smoothing=smoothing)


@pytest.mark.parametrize("vol_window", [0, -10])
def test_volatility_window_non_positive_raises(vol_window: int) -> None:
    with pytest.raises(ValueError, match="volatility_window"):
        FRAMAChannel(volatility_window=vol_window)


# ---------- 4. determinism --------------------------------------------------


def test_compute_is_deterministic() -> None:
    bars = _random_ohlcv(400, seed=11)
    fc = FRAMAChannel(length=26, distance=1.5)
    a = fc.compute(bars)
    b = fc.compute(bars)
    assert a.equals(b)


# ---------- 5. crossover semantics ------------------------------------------


def _flat_then_breakout(n_flat: int, n_break: int) -> pl.DataFrame:
    """``n_flat`` near-constant bars followed by a strong upward leg.

    The flat region keeps volatility (= SMA of high-low) tiny, so the
    band stays glued to the FRAMA value. The breakout leg drags ``hlc3`` above
    the upper band, producing at least one ``frama_break_up`` bar.
    """
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for i in range(n_flat):
        rows.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": 100.0,
                "high": 100.05,
                "low": 99.95,
                "close": 100.0,
                "volume": 1.0,
            }
        )
    for i in range(n_break):
        p = 100.0 + (i + 1) * 1.0
        rows.append(
            {
                "timestamp": base + timedelta(hours=n_flat + i),
                "open": p - 0.5,
                "high": p + 0.5,
                "low": p - 0.5,
                "close": p,
                "volume": 1.0,
            }
        )
    return pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    )


def test_synthetic_breakout_produces_at_least_one_break_up() -> None:
    bars = _flat_then_breakout(n_flat=250, n_break=80)
    out = FRAMAChannel(length=26, distance=1.5, volatility_window=200).compute(bars)
    n_up = int(out["frama_break_up"].sum() or 0)
    n_dn = int(out["frama_break_dn"].sum() or 0)
    assert n_up >= 1, f"expected break_up signals on uptrend, got {n_up}"
    # Upward leg should not produce break_dn.
    assert n_dn == 0, f"unexpected break_dn signals on uptrend: {n_dn}"


def test_synthetic_breakdown_produces_at_least_one_break_dn() -> None:
    flat_then_up = _flat_then_breakout(n_flat=250, n_break=0)
    # Append a downward leg from the flat baseline.
    n_flat = flat_then_up.height
    base = flat_then_up["timestamp"][0]
    rows = []
    for i in range(80):
        p = 100.0 - (i + 1) * 1.0
        rows.append(
            {
                "timestamp": base + timedelta(hours=n_flat + i),
                "open": p + 0.5,
                "high": p + 0.5,
                "low": p - 0.5,
                "close": p,
                "volume": 1.0,
            }
        )
    extra = pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    )
    bars = pl.concat([flat_then_up, extra])
    out = FRAMAChannel(length=26, distance=1.5, volatility_window=200).compute(bars)
    n_dn = int(out["frama_break_dn"].sum() or 0)
    n_up = int(out["frama_break_up"].sum() or 0)
    assert n_dn >= 1, f"expected break_dn signals on downtrend, got {n_dn}"
    assert n_up == 0, f"unexpected break_up signals on downtrend: {n_up}"


# ---------- 6. empty input --------------------------------------------------


def test_compute_empty_bars_returns_empty_dataframe_with_schema() -> None:
    empty = pl.DataFrame(
        {
            "timestamp": pl.Series([], dtype=pl.Datetime(time_unit="us", time_zone="UTC")),
            "open": pl.Series([], dtype=pl.Float64),
            "high": pl.Series([], dtype=pl.Float64),
            "low": pl.Series([], dtype=pl.Float64),
            "close": pl.Series([], dtype=pl.Float64),
            "volume": pl.Series([], dtype=pl.Float64),
        }
    )
    out = FRAMAChannel(length=26, distance=1.5).compute(empty)
    assert out.height == 0
    assert "frama" in out.columns
    assert "frama_break_up" in out.columns


# ---------- 7. alpha clamp / dimension contract -----------------------------


def test_alpha_is_clamped_between_0_01_and_1() -> None:
    bars = _random_ohlcv(400)
    out = FRAMAChannel(length=26, distance=1.5).compute(bars)
    a = out["frama_alpha"].drop_nulls().to_numpy()
    assert (a >= 0.01 - 1e-12).all()
    assert (a <= 1.0 + 1e-12).all()


def test_dimension_is_nan_for_first_bars_then_finite() -> None:
    bars = _flat_then_breakout(n_flat=80, n_break=80)
    out = FRAMAChannel(length=26, distance=1.5, volatility_window=50).compute(bars)
    dim = out["frama_dimension"].to_numpy()
    # First length-1 bars cannot have a dimension yet.
    assert math.isnan(dim[0])
    assert math.isnan(dim[24])
    # After warmup we expect at least some finite dimension values somewhere
    # in the breakout region (NaN if N1*N2*N3 == 0, e.g. perfectly flat).
    assert not all(math.isnan(v) for v in dim[80:])
